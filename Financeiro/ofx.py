import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass
class OFXLancamento:
    data: datetime.date
    descricao: str
    valor: Decimal
    identificador: str

    @property
    def tipo(self):
        return 'credito' if self.valor >= 0 else 'debito'


def _tag_value(block, tag):
    pattern = rf'<{tag}>(.*?)(?=<[A-Z0-9]+>|$)'
    match = re.search(pattern, block, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ''
    return re.sub(r'<.*?>', '', match.group(1)).strip()


def _parse_date(value):
    value = (value or '')[:8]
    return datetime.strptime(value, '%Y%m%d').date()


def parse_ofx(file_obj):
    raw = file_obj.read()
    if isinstance(raw, bytes):
        text = raw.decode('utf-8', errors='ignore')
    else:
        text = raw

    lancamentos = []
    for block in re.findall(r'<STMTTRN>(.*?)(?=</STMTTRN>|<STMTTRN>|</BANKTRANLIST>)', text, re.IGNORECASE | re.DOTALL):
        data = _parse_date(_tag_value(block, 'DTPOSTED'))
        valor = Decimal(_tag_value(block, 'TRNAMT').replace(',', '.'))
        descricao = _tag_value(block, 'MEMO') or _tag_value(block, 'NAME') or 'Lancamento sem descricao'
        identificador = _tag_value(block, 'FITID')
        lancamentos.append(
            OFXLancamento(
                data=data,
                descricao=descricao[:255],
                valor=valor,
                identificador=identificador[:120],
            )
        )
    return lancamentos
