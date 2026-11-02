"""Dataset sharing retains public provenance and excludes owner-only state."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from research_data.library import ResearchLibrary
from research_data.library.sharing import build_shared_library,archive_shared_library,restore_shared_library,digest_file


class SharedLibraryTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.source=self.root/'source';self.shared=self.root/'shared'
        self.library=ResearchLibrary(self.source)
        self.library.register_source('synthetic',name='Synthetic public source')
        self.library.register_dataset('synthetic.daily',provider='synthetic',scope='Synthetic test only',
            frequency='1d',required=False,units={'count':'count'},expected_fields=['count'])
        self.capture=self.library.capture('synthetic','https://example.org/synthetic',b'{"count":7}',retrieved_at='2026-10-27T00:00:00Z')
        rid=self.library.put('synthetic.daily','2026-10-01T00:00:00Z',{'count':'7'},self.capture)
        self.record=self.library.evidence(rid)
        self.secret=b'synthetic-owner-private-token-123456789'
        self.private_sha=self.library.object(self.secret)
        self.private_id='context-'+'a'*24
        with self.library.connect() as c:
            c.execute('INSERT INTO legacy VALUES(?,?)',(self.private_id,json.dumps({'profile':{'private_note':self.secret.decode()},'source':{'sha256':self.private_sha}})))
            c.execute('INSERT INTO research_outputs VALUES(?,?,?,?)',('answer-private','answers','2026-10-27T00:00:00Z',self.secret.decode()))
            c.execute('INSERT INTO output_fts VALUES(?,?)',('answer-private',self.secret.decode()))
        self.before=digest_file(self.library.path)

    def test_public_data_remains_while_private_rows_objects_and_deleted_bytes_are_removed(self):
        manifest=build_shared_library(self.source,self.shared,forbidden=[self.secret])
        copied=ResearchLibrary(self.shared,create=False)
        self.assertEqual(copied.evidence(self.record['record_id'])['values'],self.record['values'])
        self.assertEqual(copied.raw(self.capture),b'{"count":7}')
        with copied.connect(readonly=True) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM legacy').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT count(*) FROM research_outputs').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT count(*) FROM output_fts').fetchone()[0],0)
        self.assertFalse((self.shared/'objects'/self.private_sha[:2]/(self.private_sha+'.bin')).exists())
        self.assertNotIn(self.secret,(self.shared/'research.sqlite').read_bytes())
        self.assertEqual(digest_file(self.library.path),self.before)
        self.assertTrue(manifest['privacy']['source_record_ids_and_raw_bytes_unchanged'])

    def test_archive_restores_in_an_independent_directory_with_all_hashes(self):
        build_shared_library(self.source,self.shared,forbidden=[self.secret])
        archive=self.root/'dataset.zip';receipt=archive_shared_library(self.shared,archive)
        restored=self.root/'restored'
        check=restore_shared_library(archive,restored,expected_sha256=receipt['sha256'])
        self.assertEqual(check['status'],'passed')
        self.assertEqual(ResearchLibrary(restored,create=False).series('synthetic.daily')['total'],1)
        with self.assertRaisesRegex(ValueError,'Destination exists'):restore_shared_library(archive,restored)
        with self.assertRaisesRegex(ValueError,'checksum'):restore_shared_library(archive,self.root/'wrong',expected_sha256='0'*64)

    def test_nested_raw_calculation_dependencies_are_preserved(self):
        implementation=b'def calculate(): return 7\n'
        implementation_sha=self.library.object(implementation)
        nested_sha=self.library.object(json.dumps({'implementation_sha256':implementation_sha}).encode())
        capture=self.library.capture('calculation:synthetic','urn:synthetic:calculation',
            json.dumps({'calculation_manifest_sha256':nested_sha}).encode())
        self.library.put('synthetic.daily','2026-10-02T00:00:00Z',{'count':'7'},capture)
        build_shared_library(self.source,self.shared,forbidden=[self.secret])
        for sha in (nested_sha,implementation_sha):
            path=self.shared/'objects'/sha[:2]/(sha+'.bin')
            self.assertEqual(digest_file(path),sha)
        self.assertEqual((self.shared/'objects'/implementation_sha[:2]/(implementation_sha+'.bin')).read_bytes(),implementation)
        self.assertFalse((self.shared/'objects'/self.private_sha[:2]/(self.private_sha+'.bin')).exists())

    def test_live_wal_and_non_core_dependencies_prevent_sharing(self):
        wal=self.source/'research.sqlite-wal';wal.write_bytes(b'pending')
        with self.assertRaisesRegex(ValueError,'WAL'):build_shared_library(self.source,self.shared)
        wal.unlink()
        with self.library.connect() as c:
            row=json.loads(c.execute('SELECT body FROM observations').fetchone()[0]);row['input_record_ids']=[self.private_id]
            c.execute('UPDATE observations SET body=?',(json.dumps(row),))
        with self.assertRaisesRegex(ValueError,'non-core'):build_shared_library(self.source,self.shared)

    def test_archive_traversal_is_refused_before_destination_is_created(self):
        archive=self.root/'unsafe.zip'
        manifest={'schema':'aether-shared-library-v1','files':{'../outside':hashlib.sha256(b'x').hexdigest()}}
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr('SHARE_MANIFEST.json',json.dumps(manifest));z.writestr('../outside','x')
        with self.assertRaisesRegex(ValueError,'Unsafe'):restore_shared_library(archive,self.root/'restore')
        self.assertFalse((self.root/'restore').exists())

    def test_an_actual_credential_in_a_public_source_is_not_silently_redacted(self):
        self.library.capture('synthetic','https://example.org/credential',self.secret)
        with self.assertRaisesRegex(ValueError,'contents withheld'):
            build_shared_library(self.source,self.shared,forbidden=[self.secret])


if __name__=='__main__':unittest.main()
