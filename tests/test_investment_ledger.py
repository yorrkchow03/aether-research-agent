import tempfile,unittest
from decimal import Decimal
from research_agent.investment.ledger import Ledger,Transaction,account

def entry(key,kind,asset,q,price=None,fee='0',basis=None,time='2025-01-01T00:00:00Z'):
    return {'external_id':key,'time':time,'kind':kind,'asset':asset,'quantity':q,'price_usd':price,'fee_usd':fee,'cost_basis_usd':basis}

class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.ledger=Ledger(self.tmp.name)
    def test_fifo_fees_exact_cash_and_realized_unrealized(self):
        rows=[entry('cash','deposit','USD','1000'),entry('a','buy','ETH','1','100','1'),entry('b','buy','ETH','1','200','2'),entry('c','sell','ETH','1.5','300','3')]
        self.ledger.import_rows(rows);s=self.ledger.snapshot({'ETH':{'price_usd':'250','observed_at':'2025-01-02T00:00:00Z'}})
        self.assertEqual(Decimal(s['cash_usd']),1144);self.assertEqual(Decimal(s['realized_pnl_usd']),245)
        p=s['positions'][0];self.assertEqual(Decimal(p['quantity']),Decimal('.5'));self.assertEqual(Decimal(p['cost_basis_usd']),101);self.assertEqual(Decimal(p['unrealized_pnl_usd']),24)
        self.assertEqual(Decimal(s['equity_usd']),1269)
    def test_unknown_transfer_basis_and_missing_prices_never_become_zero_pnl(self):
        self.ledger.import_rows([entry('gift','transfer_in','ETH','2')]);s=self.ledger.snapshot()
        self.assertIsNone(s['equity_usd']);self.assertIsNone(s['unrealized_pnl_usd']);self.assertIsNone(s['positions'][0]['cost_basis_usd'])
        self.ledger.import_rows([entry('sale','sell','ETH','1','200')]);s=self.ledger.snapshot({'ETH':{'price_usd':'100','observed_at':'2025-01-01T00:00:00Z'}})
        self.assertIsNone(s['realized_pnl_usd']);self.assertIsNone(s['unrealized_pnl_usd'])
    def test_atomic_csv_conflicts_and_idempotency(self):
        csv='external_id,time,kind,asset,quantity,price_usd,fee_usd\na,2025-01-01T00:00:00Z,deposit,USD,500,,\nb,2025-01-01T01:00:00Z,buy,ETH,1,100,1\n'
        self.assertEqual(self.ledger.import_csv(csv)['added'],2);self.assertEqual(self.ledger.import_csv(csv)['duplicates'],2)
        with self.assertRaises(ValueError):self.ledger.import_csv(csv.replace(',500,',',600,'))
        before=len(self.ledger.list())
        with self.assertRaises(ValueError):self.ledger.import_rows([entry('good','deposit','USD','5'),entry('bad','sell','ETH','50','10')])
        self.assertEqual(len(self.ledger.list()),before)
    def test_void_is_reversible_and_cannot_break_later_balances(self):
        ids=self.ledger.import_rows([entry('cash','deposit','USD','500'),entry('buy','buy','ETH','1','100')])['ids']
        with self.assertRaises(ValueError):self.ledger.void(ids[0])
        self.assertIsNone(self.ledger.list()[0]['voided_at'])
        self.ledger.void(ids[1]);self.assertEqual(Decimal(self.ledger.snapshot()['cash_usd']),500)
        self.ledger.void(ids[1],True);self.assertEqual(Decimal(self.ledger.snapshot()['cash_usd']),400)
    def test_economic_cutoff_blocks_future_mark_and_transactions(self):
        rows=[entry('cash','deposit','USD','1000'),entry('buy','buy','ETH','1','100',time='2025-01-02T00:00:00Z')]
        s=account(rows,{'ETH':{'price_usd':'1000','observed_at':'2025-01-04T00:00:00Z'}},'2025-01-03T00:00:00Z')
        self.assertIsNone(s['equity_usd']);self.assertEqual(account(rows,as_of='2025-01-01T12:00:00Z')['cash_usd'],'1000')
        with self.assertRaises(ValueError):Transaction.model_validate(entry('future','deposit','USD','1',time='2099-01-01T00:00:00Z'))
    def test_fractional_seconds_are_sorted_as_instants(self):
        rows=[entry('buy','buy','ETH','1','100',time='2025-01-01T00:00:00.000001Z'),entry('cash','deposit','USD','100',time='2025-01-01T00:00:00Z')]
        self.assertEqual(Decimal(account(rows)['cash_usd']),0)
