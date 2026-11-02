"""One selected query window with read-only fallback for old evidence IDs."""
from pathlib import Path

from research_data.library import ResearchLibrary


class LibraryRouter:
    def __init__(self, primary, fallbacks=()):
        self.primary = primary
        self.fallbacks = tuple(item for item in fallbacks if item.root != primary.root)

    def __getattr__(self, name):
        # Catalog/query/coverage stay tied to the explicitly selected window.
        return getattr(self.primary, name)

    def _owner(self, record_id):
        for library in (self.primary, *self.fallbacks):
            if library.contains(record_id):
                return library
        raise KeyError('Evidence is absent from the selected and retained libraries')

    def contains(self, record_id):
        return any(library.contains(record_id) for library in (self.primary, *self.fallbacks))

    def evidence(self, record_id):
        return self._owner(record_id).evidence(record_id)

    def evidence_many(self, record_ids):
        pending=list(dict.fromkeys(record_ids));records={};errors={}
        for library in (self.primary,*self.fallbacks):
            if not pending:break
            packet=library.evidence_many(pending)
            records.update(packet['records'])
            for rid,reason in packet['errors'].items():
                # A damaged/missing capture of an ID owned by this library is
                # not an absent ID and must not fall through to another copy.
                if reason!='KeyError' or library.contains(rid):errors[rid]=reason
            pending=[rid for rid in pending if rid not in records and rid not in errors]
        errors.update({rid:'KeyError' for rid in pending})
        return {'records':records,'errors':errors}

    def exclusion_reasons(self, record_id):
        try:
            return self._owner(record_id).exclusion_reasons(record_id)
        except KeyError:
            return []


def open_library(root, selected='data/library'):
    root = Path(root).resolve()
    path = Path(selected)
    path = (path if path.is_absolute() else root / path).resolve()
    if not path.is_relative_to(root):
        raise ValueError('The configured research library must be within this project')
    monthly = root / 'data/library'
    # An explicit missing selection is a visible configuration error, not a
    # silent switch to a different historical interval.
    if not (path / 'research.sqlite').is_file():
        if selected == 'data/library':
            return None
        raise ValueError('Configured research library has not been built')
    primary = ResearchLibrary(path, create=False)
    fallbacks = [ResearchLibrary(monthly, create=False)] if monthly != path and (monthly / 'research.sqlite').is_file() else []
    return LibraryRouter(primary, fallbacks)
