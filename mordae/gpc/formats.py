#!/usr/bin/python -tt
# -*- coding: utf-8 -*-

from __future__ import absolute_import, division, print_function

import csv
import re


def parse_input(fp):
    lines = fp.readlines()

    if not lines:
        return Input(0, 0, [])

    if 'Pohyby na úètù' in lines[0]:
        lines[0] = lines[0][3:]
        lines = [line.decode('windows-1250').rstrip() for line in lines]
        return parse_csob(lines)

    if 'Pohyby na účtu' in lines[0]:
        lines[0] = lines[0][3:]
        lines = [line.decode('utf8').rstrip() for line in lines]
        return parse_csob(lines)

    if 'DATUM ODEPS\xc1N\xcd' in lines[0]:
        return parse_rb(lines[1:])

    return Input(0, 0, [])


def parse_rb(lines):
    input_records = list(csv.reader(lines, delimiter=';'))

    for record in input_records:
        for index, field in enumerate(record):
            record[index] = field.decode('windows-1250')

    rb_records = [RB_Record(rec) for rec in input_records]
    return Input(0, 5500, rb_records)


def parse_csob(lines):
    account = re.match(u'.*: ([0-9-]+)', lines[0]).group(1)

    input_records = u'\n'.join(lines[2:]).split('\n\n')
    input_records = filter(lambda r: r.strip(), input_records)
    csob_records = []

    for i, rec in enumerate(input_records):
        data = {}

        rec = re.sub(u'\n +', u'\nzpráva: ', rec.strip())

        for row in rec.strip().split(u'\n'):
            key, value = row.split(':', 1)
            data[key.strip()] = value.strip()

        csob_records.append(CSOB_Record(data))

    return Input(int(account), 300, csob_records)


def render_output(fp, inp, account, sequence):
    result = Set()

    for record in inp.records:
        result.add_record(record)

    result.render(fp, account, sequence)


class Input(object):
    def __init__(self, account=0, bank=0, records=[]):
        self.bank = bank
        self.account = account
        self.records = records

    def add_record(self, rec):
        self.records.append(rec)


class Record(object):
    def __init__(self, datum, poznamka='',
                 nazev_protiuctu='', cislo_protiuctu='',
                 castka=0, poplatek=0, typ='', zprava='',
                 vs=0, ks=0, ss=0):
        self.datum = datum
        self.poznamka = poznamka
        self.nazev_protiuctu = nazev_protiuctu
        self.cislo_protiuctu = cislo_protiuctu
        self.castka = castka
        self.poplatek = poplatek
        self.typ = typ
        self.zprava = zprava
        self.vs = vs
        self.ks = ks
        self.ss = ss

    def render(self, fp, account):
        d, m, y = self.datum
        datum = '%02d%02d%02d' % (d, m, y - 2000)

        our_acc, our_bank = split_account(account)
        peer_acc, peer_bank = split_account(self.cislo_protiuctu)

        poznamka = self.poznamka or self.zprava or self.typ

        fields = [
            '075',
            '%016d' % our_acc,
            '%016d' % peer_acc,
            '0' * 13,
            '%012d' % abs(self.castka),
            '1' if self.castka < 0 else '2',
            '%010d' % self.vs,
            '00%04d%04d' % (peer_bank, self.ks),
            '%010d' % self.ss,
            datum,
            '%-20s' % poznamka[:20].encode('windows-1250'),
            '0',
            '0203',
            datum,
        ]

        fp.write(''.join(fields) + '\r\n')


class RB_Record(Record):
    def __init__(self, rec):
        datum, cas, poznamka, nazev_protiuctu, cislo_protiuctu, \
            datum_odepsani, _valuta, typ, transakce, vs, ks, ss, \
            castka, poplatek, smena, zprava = rec

        self.datum = tuple(int(x) for x in datum_odepsani.split(u'.'))

        self.poznamka = poznamka
        self.nazev_protiuctu = nazev_protiuctu
        self.cislo_protiuctu = cislo_protiuctu

        castka = castka or '0'
        poplatek = poplatek or '0'

        castka = int(100 * float(castka.replace(',', '.').replace(' ', '')))
        self.castka = castka

        poplatek = int(100 * float(poplatek.replace(',', '.').replace(' ', '')))
        self.poplatek = poplatek

        self.typ = typ
        self.zprava = zprava

        self.vs = int(vs or '0')
        self.ks = int(ks or '0')
        self.ss = int(ss or '0')

    def __repr__(self):
        return '<RB_Record %s>' % (self.__dict__,)


class CSOB_Record(Record):
    def __init__(self, data):
        datum = data[u'datum zaúčtování']

        self.datum = tuple(int(x) for x in datum.split(u'.'))

        self.nazev_protiuctu = data[u'název protiúčtu']
        self.cislo_protiuctu = data[u'protiúčet']

        self.castka = int(100 * float(data[u'částka']))
        self.poplatek = 0

        self.typ = data[u'označení operace']
        self.zprava = data.get(u'zpráva', '')
        self.poznamka = data[u'poznámka']

        self.vs = int(data[u'variabilní symbol'] or '0')
        self.ks = int(data[u'konstantní symbol'] or '0')
        self.ss = int(data[u'specifický symbol'] or '0')

    def __repr__(self):
        return '<CSOB_Record %s>' % (self.__dict__,)


class Group(object):
    def __init__(self):
        self.records = []

    def add_record(self, rec):
        self.records.append(rec)

    def render(self, fp, account, sequence):
        records = sorted(self.records, key=lambda rec: xreversed(rec.datum))

        # Date for the synthetic, bank fee records.
        fee_date = records[-1].datum

        # Add all fees.
        fees = sum([rec.poplatek for rec in records])

        # Remove fee-only records.
        records = [rec for rec in records if rec.castka]

        # Create a sythetic record for fees only if non-zero.
        if fees:
            fr = Record(datum=fee_date, castka=fees, cislo_protiuctu=account, typ=u'Poplatky')
            records.append(fr)

        # Abort if we have no records.
        if not records:
            return

        # Calculate group totals.
        debet = sum([rec.castka for rec in records if rec.castka < 0])
        kredit = sum([rec.castka for rec in records if rec.castka >= 0])

        # Parse account string to two numbers.
        acc, bank = split_account(account)

        # For the output below.
        date = records[-1].datum

        # Date for the header record.
        fields = [
            '074',
            '%016d' % acc,
            '%-20s' % ('Pohyby %d.%d.%d' % date),
            '%06d' % 0,
            '%014d+' % 0,
            '%014d+' % 0,
            '%014d0' % abs(debet),
            '%014d0' % abs(kredit),
            '%03d' % sequence,
            '%02d%02d%02d' % (date[:2] + (date[2] - 2000,)),
            ' ' * 14,
        ]

        fp.write(''.join(fields) + '\r\n')
        for record in records:
            record.render(fp, account)


def split_account(acc):
    if not acc:
        return 0, 0

    m = re.match(u'^((\d+)-)?(\d+)/(\d+)$', acc)
    acc = int('%06d%010d' % (int(m.group(2) or 0), int(m.group(3))))
    bank = int(m.group(4))
    return acc, bank


def xreversed(lst):
    return list(reversed(lst))


class Set(object):
    def __init__(self):
        self.groups = {}

    def add_record(self, rec):
        self.groups.setdefault(rec.datum, Group()).add_record(rec)

    def render(self, fp, account, sequence):
        for i, date in enumerate(sorted(self.groups, key=xreversed)):
            self.groups[date].render(fp, account, sequence + i)


# vim:set sw=4 ts=4 et:
