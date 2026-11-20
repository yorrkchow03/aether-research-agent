import unittest

from research_agent.passage_retrieval import retrieve_passages
from research_agent.tool_result_views import _passage_packet
from research_agent.research_state import derive_observation


def source(slug, title, text, *, url=None, retrieved='2026-10-17T00:00:00Z'):
    return {'record_id': 'page-'+slug, 'title': title, 'text': text,
            'url': url or 'https://example.org/'+slug, 'assets': ['ETH'],
            'published_at': '2026-10-16T00:00:00Z', 'retrieved_at': retrieved}


class FacetMemoryTests(unittest.TestCase):
    def test_facet_has_room_despite_duplicate_news_and_survives_view(self):
        text = 'Hoodi activation is planned. ' * 19
        records = [
            source('aaa', 'Hoodi activation', text),
            source('bbb', 'Hoodi activation', text),
            source('ccc', 'Validator custody', 'Validator custody responsibility remains with operators.'),
        ]
        result = retrieve_passages(records, 'Hoodi activation validator custody',
                                   facet_queries=['Hoodi activation', 'Validator custody'], limit=2)
        self.assertIn('page-ccc', result['source_ids'])
        self.assertEqual(len(result['source_ids']), 2)
        packet = _passage_packet(result)
        self.assertEqual(len(packet['memory_bundles']['facets']), 2)
        for item in result['results']:
            original = next(c for c in records if c['record_id']==item['record_id'])
            self.assertEqual(item['excerpt'], original['text'][item['start']:item['end']])

    def test_now_known_correction_is_grouped_and_future_relation_is_excluded(self):
        records = [source('111111111111','Hoodi old schedule','Hoodi scheduled for 21 October.'),
                   source('222222222222','Hoodi update','Hoodi has been postponed; new date pending.'),
                   source('333333333333','Hoodi later update','Hoodi scheduled for December.',
                          retrieved='2026-10-24T00:00:00Z')]
        relations = [
            {'subject':'page-222222222222','object':'page-111111111111','predicate':'corrects','known_at':'2026-10-17T00:00:00Z'},
            {'subject':'page-333333333333','object':'page-222222222222','predicate':'supersedes','known_at':'2026-10-24T00:00:00Z'}]
        common = dict(facet_queries=['Hoodi schedule'], mode='strict',
                      cutoff='2026-10-20T00:00:00Z', relations=relations)
        enough = retrieve_passages(records, 'Hoodi', limit=2, **common)
        self.assertEqual(set(enough['source_ids']), {'page-111111111111', 'page-222222222222'})
        self.assertEqual(enough['memory_bundles']['relations'], relations[:1])
        change=derive_observation('recall_memory',enough,
                                  artifact_id='artifact-test',observation_id='observation-test')
        link,=change['conflicts']
        self.assertEqual((link['subject'],link['relation'],link['object']),
                         ('page-222222222222','corrects','page-111111111111'))
        self.assertEqual(link['status'],'requires_source_check')
        small = retrieve_passages(records, 'Hoodi', limit=1, **common)
        self.assertEqual(small['source_ids'], [])
        self.assertTrue(any(row['reason']=='related_evidence_group_exceeds_context' for row in small['gaps']))


    def test_shared_asset_words_do_not_cover_an_unrelated_facet(self):
        records = [source('staking','Ethereum staking validators',
                          'Ethereum staking validators earn validator rewards.'),
                   source('gov','Ethereum governance vote',
                          'Ethereum governance vote proposes a policy update.'),
                   source('market','Ethereum daily market',
                          'Ethereum daily market price volume increased.')]
        result = retrieve_passages(records, 'Ethereum daily market price volume',
                                   facet_queries=['Ethereum staking validators','Ethereum governance vote'],
                                   limit=2)
        self.assertEqual(set(result['source_ids']), {'page-staking','page-gov'})

    def test_empty_facet_has_an_explicit_gap_state(self):
        result=retrieve_passages([source('one','Hoodi','Hoodi planned')], 'unrelated',
                                 facet_queries=['Validator custody'], limit=2)
        self.assertEqual(result['memory_bundles']['facets'][0]['state'], 'no_candidate_selected')
