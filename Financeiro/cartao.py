import csv
import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass
class CartaoLancamento:
    data: datetime.date
    descricao: str
    valor: Decimal
    identificador: str

    @property
    def tipo(self):
        return 'credito' if self.valor >= 0 else 'debito'


def _decode_csv(raw):
    for encoding in ('utf-8-sig', 'latin-1'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='ignore')


def _parse_decimal(value):
    cleaned = (value or '').strip().replace('R$', '').replace(' ', '')
    if ',' in cleaned and '.' in cleaned:
        cleaned = cleaned.replace('.', '').replace(',', '.')
    else:
        cleaned = cleaned.replace(',', '.')
    return Decimal(cleaned)


def _identificador(data, descricao, valor, index):
    base = f'{data.isoformat()}|{descricao}|{valor}|{index}'
    return f'cartao-{hashlib.sha1(base.encode("utf-8")).hexdigest()[:32]}'


def parse_cartao_csv(file_obj):
    raw = file_obj.read()
    if isinstance(raw, bytes):
        text = _decode_csv(raw)
    else:
        text = raw

    lancamentos = []
    reader = csv.reader(text.splitlines())
    for index, row in enumerate(reader, start=1):
        if not row or len(row) < 3:
            continue
        if index == 1 and row[0].strip().lower() == 'data':
            continue

        data = datetime.strptime(row[0].strip(), '%Y-%m-%d').date()
        descricao = row[1].strip() or 'Lancamento sem descricao'
        valor = _parse_decimal(row[2])
        if valor > 0:
            valor = -valor

        lancamentos.append(
            CartaoLancamento(
                data=data,
                descricao=descricao[:255],
                valor=valor,
                identificador=_identificador(data, descricao, valor, index),
            )
        )
    return lancamentos
