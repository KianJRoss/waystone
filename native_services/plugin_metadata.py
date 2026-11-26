"""Read-only native plugin game metadata; no source profile or Windows runtime needed."""
import json
from pathlib import Path
import sqlite3

APPLICATION_ID = 0x504D4431

class PluginMetadata:
    def __init__(self, path):
        self.db = sqlite3.connect(Path(path).absolute().as_uri() + '?mode=ro', uri=True)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA query_only=ON')
        if self.db.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID:
            self.close()
            raise ValueError('Not a plugin metadata repository')
    def close(self):
        self.db.close()
    def records(self, canonical_id=None, dataset=None, unresolved=False):
        clauses, args = [], []
        for field, value in [('canonical_id', canonical_id), ('dataset', dataset)]:
            if value is not None:
                clauses.append(field + '=?'); args.append(value)
        if unresolved: clauses.append('canonical_id IS NULL')
        sql = 'SELECT * FROM records' + (' WHERE ' + ' AND '.join(clauses) if clauses else '') + ' ORDER BY source_path'
        for row in self.db.execute(sql, args):
            result = dict(row)
            result['document'] = json.loads(result['raw'])
            yield result
    def provenance(self):
        return json.loads(self.db.execute('SELECT report_json FROM provenance').fetchone()[0])
    def archives(self, canonical_id=None, dataset=None):
        """Custom audit/snapshot/cache artifacts; exact raw bytes and indexed source IDs."""
        clauses, args = [], []
        if canonical_id is not None:
            clauses.append('source_path IN (SELECT source_path FROM archive_links WHERE canonical_id=?)'); args.append(canonical_id)
        if dataset is not None: clauses.append('dataset=?'); args.append(dataset)
        sql='SELECT * FROM archive_files'+(' WHERE '+' AND '.join(clauses) if clauses else '')+' ORDER BY source_path'
        for row in self.db.execute(sql,args):
            result=dict(row);result['document']=json.loads(result['raw'])
            result['links']=[dict(r) for r in self.db.execute('SELECT * FROM archive_links WHERE source_path=? ORDER BY entry_index',(row['source_path'],))]
            yield result
