"""Private, append-only USD ledger. FIFO is an accounting convention, not tax advice."""
from __future__ import annotations
import csv,hashlib,io,json,sqlite3,uuid
from contextlib import contextmanager
from datetime import datetime,timezone
from decimal import Decimal,InvalidOperation
from pathlib import Path
from typing import Literal
from pydantic import BaseModel,Field,ConfigDict,model_validator

ZERO=Decimal(0)
def dec(value):
    try:d=Decimal(str(value))
    except InvalidOperation:raise ValueError('Invalid decimal amount') from None
    if not d.is_finite():raise ValueError('Amounts must be finite')
    return d

def instant(value):
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    if d.tzinfo is None:raise ValueError('Transaction time must include an offset')
    return d.astimezone(timezone.utc).isoformat().replace('+00:00','Z')

class Transaction(BaseModel):
    model_config=ConfigDict(extra='forbid')
    external_id:str=Field(min_length=1,max_length=120)
    time:str
    kind:Literal['deposit','withdrawal','buy','sell','transfer_in','transfer_out']
    asset:Literal['USD','ETH','BTC']
    quantity:str
    price_usd:str|None=None
    fee_usd:str='0'
    cost_basis_usd:str|None=None
    note:str=Field(default='',max_length=300)

    @model_validator(mode='after')
    def valid(self):
        self.time=instant(self.time);q=dec(self.quantity);fee=dec(self.fee_usd)
        if q<=0 or fee<0:raise ValueError('Quantity must be positive; fee cannot be negative')
        if q>Decimal('1e12') or fee>Decimal('1e12'):raise ValueError('Amount exceeds the supported ledger limit')
        if datetime.fromisoformat(self.time.replace('Z','+00:00'))>datetime.now(timezone.utc):raise ValueError('Future transactions belong in scenarios, not an actual ledger')
        self.quantity=str(q.normalize());self.fee_usd=str(fee.normalize())
        if self.kind in ('deposit','withdrawal'):
            if self.asset!='USD' or self.price_usd is not None or self.cost_basis_usd is not None:raise ValueError('Cash flows must be USD without a price/basis')
        else:
            if self.asset=='USD':raise ValueError('Trade/transfer asset must be ETH or BTC')
            if self.kind in ('buy','sell'):
                if self.price_usd is None or not 0<dec(self.price_usd)<=Decimal('1e9'):raise ValueError('A trade requires an execution price in the supported positive range')
                if self.cost_basis_usd is not None:raise ValueError('Trade basis is derived from FIFO, not supplied')
                self.price_usd=str(dec(self.price_usd).normalize())
            elif self.price_usd is not None:raise ValueError('Transfers do not execute at a price')
            if self.kind!='transfer_in' and self.cost_basis_usd is not None:raise ValueError('Only an incoming transfer can supply its total basis')
        if self.cost_basis_usd is not None:
            if not 0<=dec(self.cost_basis_usd)<=Decimal('1e21'):raise ValueError('Basis must be within the supported nonnegative range')
            self.cost_basis_usd=str(dec(self.cost_basis_usd).normalize())
        return self


def account(transactions,marks=None,as_of=None):
    """Replay in economic-time order; unknown lots propagate unknown PnL, never zero."""
    cutoff=instant(as_of) if as_of else None;cash=ZERO;lots={};realized={};fees=ZERO;flows=ZERO;unknown_disposals=set()
    for index,raw in sorted(enumerate(transactions),key=lambda pair:(datetime.fromisoformat(instant(pair[1]['time']).replace('Z','+00:00')),pair[0])):
        t=Transaction.model_validate(raw)
        if cutoff and datetime.fromisoformat(t.time.replace('Z','+00:00'))>datetime.fromisoformat(cutoff.replace('Z','+00:00')):continue
        q=dec(t.quantity);fee=dec(t.fee_usd);asset=t.asset;fees+=fee
        if t.kind=='deposit':cash+=q-fee;flows+=q
        elif t.kind=='withdrawal':cash-=q+fee;flows-=q
        elif t.kind=='buy':
            total=q*dec(t.price_usd)+fee;cash-=total;lots.setdefault(asset,[]).append([q,total])
        elif t.kind=='transfer_in':
            cash-=fee;basis=dec(t.cost_basis_usd)+fee if t.cost_basis_usd is not None else None
            lots.setdefault(asset,[]).append([q,basis])
        else:
            available=sum((lot[0] for lot in lots.get(asset,[])),ZERO)
            if q>available:raise ValueError(f'{asset} disposal exceeds recorded holdings at {t.time}')
            remaining=q;basis=ZERO;known=True
            for lot in lots[asset]:
                take=min(lot[0],remaining)
                if not take:continue
                allocated=lot[1]*take/lot[0] if lot[1] is not None else None
                if allocated is None:known=False
                else:basis+=allocated;lot[1]-=allocated
                lot[0]-=take;remaining-=take
                if not remaining:break
            lots[asset]=[lot for lot in lots[asset] if lot[0]>0]
            if t.kind=='sell':
                proceeds=q*dec(t.price_usd)-fee;cash+=proceeds
                if known:realized[asset]=realized.get(asset,ZERO)+proceeds-basis
                else:unknown_disposals.add(asset)
            else:cash-=fee  # transfer-out removes lots without realizing profit
        if cash<0:raise ValueError(f'Cash balance would be negative at {t.time}; import the funding history first')
    positions=[];all_marked=True;total_mark=ZERO;all_unrealized=True;unrealized=ZERO
    for asset in sorted(set(lots)|set(realized)|unknown_disposals):
        held=lots.get(asset,[]);quantity=sum((l[0] for l in held),ZERO);known=all(l[1] is not None for l in held)
        basis=sum((l[1] for l in held),ZERO) if known else None
        mark=(marks or {}).get(asset);price=dec(mark['price_usd']) if mark else None
        if mark and cutoff and datetime.fromisoformat(instant(mark['observed_at']).replace('Z','+00:00'))>datetime.fromisoformat(cutoff.replace('Z','+00:00')):price=None
        if price is not None and price<=0:raise ValueError('Mark price must be positive')
        value=quantity*price if price is not None else ZERO if quantity==0 else None
        pnl=value-basis if value is not None and basis is not None else None
        if value is None:all_marked=False
        else:total_mark+=value
        if pnl is None:all_unrealized=False
        else:unrealized+=pnl
        positions.append({'asset':asset,'quantity':str(quantity),'cost_basis_usd':str(basis) if basis is not None else None,'basis_known':known,'mark_price_usd':str(price) if price is not None else None,'market_value_usd':str(value) if value is not None else None,'unrealized_pnl_usd':str(pnl) if pnl is not None else None,'realized_pnl_usd':None if asset in unknown_disposals else str(realized.get(asset,ZERO)),'mark_source':mark if price is not None else None})
    return {'cash_usd':str(cash),'positions':positions,'net_cash_contributions_usd':str(flows),'fees_usd':str(fees),'equity_usd':str(cash+total_mark) if all_marked else None,'realized_pnl_usd':None if unknown_disposals else str(sum(realized.values(),ZERO)),'unrealized_pnl_usd':str(unrealized) if all_unrealized else None,'cost_basis_method':'FIFO; buy fees capitalized, sell fees reduce proceeds; transfers preserve supplied or unknown basis','scope':'Private manual ledger, not an exchange balance or tax filing; dividends, staking, derivatives and FX accounting are not included','as_of':cutoff}


class Ledger:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.path=self.root/'ledger.sqlite'
        with self.db() as c:c.executescript('CREATE TABLE IF NOT EXISTS entries(id TEXT PRIMARY KEY, external_id TEXT UNIQUE NOT NULL, body TEXT NOT NULL, digest TEXT NOT NULL, created_at TEXT NOT NULL, voided_at TEXT);')
    @contextmanager
    def db(self):
        c=sqlite3.connect(self.path,timeout=15);c.row_factory=sqlite3.Row;c.execute('PRAGMA journal_mode=WAL')
        try:
            with c:yield c
        finally:c.close()
    def list(self,include_voided=True):
        with self.db() as c:rows=c.execute('SELECT * FROM entries ORDER BY rowid').fetchall()
        out=[]
        for row in rows:
            if not include_voided and row['voided_at']:continue
            if hashlib.sha256(row['body'].encode()).hexdigest()!=row['digest']:raise ValueError('Ledger receipt changed')
            out.append({**json.loads(row['body']),'id':row['id'],'created_at':row['created_at'],'voided_at':row['voided_at']})
        return out
    def import_rows(self,rows):
        if not 1<=len(rows)<=5000:raise ValueError('Import 1–5000 transactions at a time')
        parsed=[Transaction.model_validate(row).model_dump() for row in rows];added=[];duplicates=0;now=datetime.now(timezone.utc).isoformat()
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            current=[json.loads(r['body']) for r in c.execute('SELECT body FROM entries WHERE voided_at IS NULL ORDER BY rowid')]
            for entry in parsed:
                body=json.dumps(entry,sort_keys=True,separators=(',',':'));digest=hashlib.sha256(body.encode()).hexdigest()
                old=c.execute('SELECT digest FROM entries WHERE external_id=?',(entry['external_id'],)).fetchone()
                if old:
                    if old['digest']!=digest:raise ValueError('Conflicting duplicate external_id: '+entry['external_id'])
                    duplicates+=1;continue
                rid=uuid.uuid4().hex;added.append(rid);current.append(entry)
                c.execute('INSERT INTO entries VALUES(?,?,?,?,?,NULL)',(rid,entry['external_id'],body,digest,now))
            account(current)  # atomic batch validation; a rejected row rolls everything back
        return {'added':len(added),'duplicates':duplicates,'ids':added}
    def import_csv(self,text):
        if len(text.encode())>2_000_000:raise ValueError('CSV exceeds 2 MB')
        reader=csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
        required={'external_id','time','kind','asset','quantity'}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):raise ValueError('Missing CSV columns: '+', '.join(sorted(required)))
        return self.import_rows([{k:(v if v!='' else None) for k,v in row.items() if k is not None and not(k in ('fee_usd','note') and v=='')} for row in reader])
    def void(self,rid,restore=False):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT * FROM entries WHERE id=?',(rid,)).fetchone()
            if not row:raise ValueError('Unknown ledger entry')
            c.execute('UPDATE entries SET voided_at=? WHERE id=?',(None if restore else datetime.now(timezone.utc).isoformat(),rid))
            account([json.loads(r['body']) for r in c.execute('SELECT body FROM entries WHERE voided_at IS NULL ORDER BY rowid')])
        return {'status':'restored' if restore else 'voided','id':rid}
    def snapshot(self,marks=None,as_of=None):
        rows=[{k:v for k,v in r.items() if k in Transaction.model_fields} for r in self.list(False)]
        return {**account(rows,marks,as_of),'transaction_count':len(rows),'empty':not rows}
    def csv(self):
        buf=io.StringIO();writer=csv.DictWriter(buf,fieldnames=list(Transaction.model_fields));writer.writeheader()
        for row in self.list(False):
            data={k:row[k] for k in Transaction.model_fields}
            for key,value in data.items():
                if isinstance(value,str) and value.startswith(('=','+','-','@','\t','\r')) and key in ('external_id','note'):data[key]="'"+value
            writer.writerow(data)
        return buf.getvalue()
