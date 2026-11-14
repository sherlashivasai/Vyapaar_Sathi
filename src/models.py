from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, Date, DateTime, Float, Integer, Numeric, String, Text

from src.db import Base

SETTLEMENT_TABLE_NAME = "merchant_settlement_reports"
SETTLEMENT_PRIMARY_KEY_COLUMNS = ("phonepereferenceid", "instrument")
SETTLEMENT_COLUMNS = (
    "paymenttype",
    "merchantreferenceid",
    "phonepereferenceid",
    "from",
    "instrument",
    "creationdate",
    "transactiondate",
    "settlementdate",
    "bankreferenceno",
    "amount",
    "fee",
    "igst",
    "cgst",
    "sgst",
)


class KhataLedger(Base):
    __tablename__ = "khata_ledger"

    id = Column(Integer, primary_key=True, index=True)
    merchant_id = Column(String, index=True, nullable=False)
    name = Column(String, index=True, nullable=False)
    phone_no = Column(String, nullable=True)
    amount = Column(Float, default=0.0, nullable=False)
    date_given = Column(Date, nullable=True)
    promised_return_date = Column(Date, nullable=True)


class EnvIntelligenceCache(Base):
    __tablename__ = "env_intelligence_cache"

    cache_key = Column(String, primary_key=True, index=True)
    store_location = Column(String, index=True)
    shop_category = Column(String)
    payload_json = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class MerchantSettlementReport(Base):
    __tablename__ = SETTLEMENT_TABLE_NAME

    paymenttype = Column(String)
    merchantreferenceid = Column(String)
    phonepereferenceid = Column(String, primary_key=True, nullable=False)
    source = Column("from", String)
    instrument = Column(String, primary_key=True, nullable=False)
    creationdate = Column(String)
    transactiondate = Column(String)
    settlementdate = Column(String)
    bankreferenceno = Column(String)
    amount = Column(Numeric(18, 6))
    fee = Column(Numeric(18, 6))
    igst = Column(Numeric(18, 6))
    cgst = Column(Numeric(18, 6))
    sgst = Column(Numeric(18, 6))
