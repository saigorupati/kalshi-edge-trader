"""
DynamoDB client and CRUD helpers for the Kalshi trading bot.

Tables:
  kalshi-calibration  — NBM forecast records + actual high settlements
  kalshi-trades       — Full trade log with PnL tracking
  kalshi-daily-pnl    — End-of-day PnL snapshots

Run standalone to create tables:
  python -m db.dynamo
"""

import time
import uuid
import logging
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from config import (
    AWS_REGION,
    AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY,
    DYNAMO_CALIBRATION_TABLE,
    DYNAMO_TRADES_TABLE,
    DYNAMO_DAILY_PNL_TABLE,
    CALIBRATION_TTL_DAYS,
    TRADES_TTL_DAYS,
)

logger = logging.getLogger(__name__)


def _ttl_epoch(days_from_now: int) -> int:
    return int((datetime.now(timezone.utc) + timedelta(days=days_from_now)).timestamp())


def _to_decimal(value) -> Decimal:
    """Convert float to Decimal for DynamoDB storage."""
    if value is None:
        return None
    return Decimal(str(round(float(value), 6)))


def _from_decimal(value) -> Optional[float]:
    """Convert DynamoDB Decimal back to float."""
    if value is None:
        return None
    return float(value)


class DynamoClient:
    def __init__(self):
        kwargs = {"region_name": AWS_REGION}
        if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
            kwargs["aws_access_key_id"] = AWS_ACCESS_KEY_ID
            kwargs["aws_secret_access_key"] = AWS_SECRET_ACCESS_KEY

        self.resource = boto3.resource("dynamodb", **kwargs)
        self.client = boto3.client("dynamodb", **kwargs)

        self._calibration = self.resource.Table(DYNAMO_CALIBRATION_TABLE)
        self._trades = self.resource.Table(DYNAMO_TRADES_TABLE)
        self._daily_pnl = self.resource.Table(DYNAMO_DAILY_PNL_TABLE)

    # ------------------------------------------------------------------
    # Table management
    # ------------------------------------------------------------------

    def ensure_tables_exist(self) -> None:
        """Create all three tables if they don't already exist."""
        self._create_calibration_table()
        self._create_trades_table()
        self._create_daily_pnl_table()
        logger.info("DynamoDB tables verified/created.")

    def _create_calibration_table(self) -> None:
        try:
            self.client.create_table(
                TableName=DYNAMO_CALIBRATION_TABLE,
                KeySchema=[
                    {"AttributeName": "city", "KeyType": "HASH"},
                    {"AttributeName": "forecast_date_cycle", "KeyType": "RANGE"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "city", "AttributeType": "S"},
                    {"AttributeName": "forecast_date_cycle", "AttributeType": "S"},
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            waiter = self.client.get_waiter("table_exists")
            waiter.wait(TableName=DYNAMO_CALIBRATION_TABLE)
            # Enable TTL
            self.client.update_time_to_live(
                TableName=DYNAMO_CALIBRATION_TABLE,
                TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"},
            )
            logger.info("Created table: %s", DYNAMO_CALIBRATION_TABLE)
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceInUseException":
                raise

    def _create_trades_table(self) -> None:
        try:
            self.client.create_table(
                TableName=DYNAMO_TRADES_TABLE,
                KeySchema=[
                    {"AttributeName": "trade_id", "KeyType": "HASH"},
                    {"AttributeName": "timestamp", "KeyType": "RANGE"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "trade_id", "AttributeType": "S"},
                    {"AttributeName": "timestamp", "AttributeType": "S"},
                    {"AttributeName": "city", "AttributeType": "S"},
                    {"AttributeName": "trade_date", "AttributeType": "S"},
                ],
                GlobalSecondaryIndexes=[
                    {
                        "IndexName": "city-date-index",
                        "KeySchema": [
                            {"AttributeName": "city", "KeyType": "HASH"},
                            {"AttributeName": "trade_date", "KeyType": "RANGE"},
                        ],
                        "Projection": {"ProjectionType": "ALL"},
                    }
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            waiter = self.client.get_waiter("table_exists")
            waiter.wait(TableName=DYNAMO_TRADES_TABLE)
            self.client.update_time_to_live(
                TableName=DYNAMO_TRADES_TABLE,
                TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"},
            )
            logger.info("Created table: %s", DYNAMO_TRADES_TABLE)
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceInUseException":
                raise

    def _create_daily_pnl_table(self) -> None:
        try:
            self.client.create_table(
                TableName=DYNAMO_DAILY_PNL_TABLE,
                KeySchema=[
                    {"AttributeName": "date", "KeyType": "HASH"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "date", "AttributeType": "S"},
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            waiter = self.client.get_waiter("table_exists")
            waiter.wait(TableName=DYNAMO_DAILY_PNL_TABLE)
            logger.info("Created table: %s", DYNAMO_DAILY_PNL_TABLE)
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceInUseException":
                raise

    # ------------------------------------------------------------------
    # Calibration records
    # ------------------------------------------------------------------

    def put_calibration(
        self,
        city: str,
        forecast_date: str,  # "YYYY-MM-DD"
        cycle: str,           # "01", "07", "13", "19"
        nbm_mu: float,
        nbm_sigma: float,
        nws_sanity_check: Optional[float] = None,
    ) -> None:
        sk = f"{forecast_date}#{cycle}"
        item = {
            "city": city,
            "forecast_date_cycle": sk,
            "forecast_date": forecast_date,
            "cycle": cycle,
            "nbm_mu": _to_decimal(nbm_mu),
            "nbm_sigma": _to_decimal(nbm_sigma),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "ttl": _ttl_epoch(CALIBRATION_TTL_DAYS),
        }
        if nws_sanity_check is not None:
            item["nws_sanity_check"] = _to_decimal(nws_sanity_check)
        self._calibration.put_item(Item=item)
        logger.debug("Stored calibration: city=%s date=%s cycle=%s", city, forecast_date, cycle)

    def update_calibration_actual(
        self,
        city: str,
        forecast_date: str,
        cycle: str,
        actual_high: float,
    ) -> None:
        sk = f"{forecast_date}#{cycle}"
        self._calibration.update_item(
            Key={"city": city, "forecast_date_cycle": sk},
            UpdateExpression="SET actual_high = :v",
            ExpressionAttributeValues={":v": _to_decimal(actual_high)},
        )

    def get_calibration_history(self, city: str, lookback_days: int = 30) -> List[dict]:
        """Return calibration records with actual_high for the last N days."""
        cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
        resp = self._calibration.query(
            KeyConditionExpression=Key("city").eq(city)
            & Key("forecast_date_cycle").begins_with(cutoff[:7]),  # year-month prefix
        )
        records = []
        for item in resp.get("Items", []):
            if item.get("actual_high") is None:
                continue
            if item["forecast_date"] < cutoff:
                continue
            records.append(
                {
                    "city": item["city"],
                    "forecast_date": item["forecast_date"],
                    "cycle": item["cycle"],
                    "nbm_mu": _from_decimal(item["nbm_mu"]),
                    "nbm_sigma": _from_decimal(item["nbm_sigma"]),
                    "actual_high": _from_decimal(item["actual_high"]),
                }
            )
        return records

    # ------------------------------------------------------------------
    # Trade log
    # ------------------------------------------------------------------

    def put_trade(self, trade: dict) -> str:
        """
        Insert a trade record. Returns the trade_id.
        Expected keys in trade: city, ticker, side, action, count, price_cents,
        model_prob, edge, kelly_fraction, dollar_risk, mode, order_id (optional).
        """
        trade_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        trade_date = now.date().isoformat()

        item = {
            "trade_id": trade_id,
            "timestamp": now.isoformat(),
            "trade_date": trade_date,
            "city": trade["city"],
            "ticker": trade["ticker"],
            "side": trade.get("side", "yes"),
            "action": trade.get("action", "buy"),
            "count": trade["count"],
            "price_cents": trade["price_cents"],
            "model_prob": _to_decimal(trade["model_prob"]),
            "edge": _to_decimal(trade["edge"]),
            "kelly_fraction": _to_decimal(trade["kelly_fraction"]),
            "dollar_risk": _to_decimal(trade["dollar_risk"]),
            "mode": trade["mode"],
            "order_id": trade.get("order_id", ""),
            "resolved": False,
            "resolved_yes": None,
            "pnl": None,
            "ttl": _ttl_epoch(TRADES_TTL_DAYS),
        }
        self._trades.put_item(Item=item)
        logger.info(
            "Logged trade %s | %s | %s | count=%d | price=%d¢ | edge=%.1f%%",
            trade_id[:8],
            trade["city"],
            trade["ticker"],
            trade["count"],
            trade["price_cents"],
            trade["edge"] * 100,
        )
        return trade_id

    def mark_trade_resolved(
        self,
        trade_id: str,
        timestamp: str,
        resolved_yes: bool,
        pnl: float,
    ) -> None:
        self._trades.update_item(
            Key={"trade_id": trade_id, "timestamp": timestamp},
            UpdateExpression="SET resolved = :r, resolved_yes = :y, pnl = :p",
            ExpressionAttributeValues={
                ":r": True,
                ":y": resolved_yes,
                ":p": _to_decimal(pnl),
            },
        )

    def get_open_trades(self, city: Optional[str] = None) -> List[dict]:
        """Scan for unresolved trades. Optionally filter by city."""
        if city:
            resp = self._trades.query(
                IndexName="city-date-index",
                KeyConditionExpression=Key("city").eq(city),
                FilterExpression="resolved = :f",
                ExpressionAttributeValues={":f": False},
            )
        else:
            resp = self._trades.scan(
                FilterExpression="resolved = :f",
                ExpressionAttributeValues={":f": False},
            )
        return self._deserialize_trades(resp.get("Items", []))

    def get_daily_trades(self, date_str: str, city: Optional[str] = None) -> List[dict]:
        """Get all trades for a given date, optionally filtered by city."""
        if city:
            resp = self._trades.query(
                IndexName="city-date-index",
                KeyConditionExpression=Key("city").eq(city) & Key("trade_date").eq(date_str),
            )
        else:
            resp = self._trades.scan(
                FilterExpression="trade_date = :d",
                ExpressionAttributeValues={":d": date_str},
            )
        return self._deserialize_trades(resp.get("Items", []))

    def _deserialize_trades(self, items: list) -> List[dict]:
        result = []
        for item in items:
            result.append(
                {
                    "trade_id": item["trade_id"],
                    "timestamp": item["timestamp"],
                    "trade_date": item["trade_date"],
                    "city": item["city"],
                    "ticker": item["ticker"],
                    "count": int(item["count"]),
                    "price_cents": int(item["price_cents"]),
                    "model_prob": _from_decimal(item.get("model_prob")),
                    "edge": _from_decimal(item.get("edge")),
                    "kelly_fraction": _from_decimal(item.get("kelly_fraction")),
                    "dollar_risk": _from_decimal(item.get("dollar_risk")),
                    "mode": item["mode"],
                    "order_id": item.get("order_id", ""),
                    "resolved": item.get("resolved", False),
                    "resolved_yes": item.get("resolved_yes"),
                    "pnl": _from_decimal(item.get("pnl")),
                }
            )
        return result

    # ------------------------------------------------------------------
    # Daily PnL
    # ------------------------------------------------------------------

    def put_daily_pnl(
        self,
        date_str: str,
        starting_balance: float,
        ending_balance: float,
        realized_pnl: float,
        win_count: int,
        loss_count: int,
        kill_switch_triggered: bool = False,
    ) -> None:
        self._daily_pnl.put_item(
            Item={
                "date": date_str,
                "starting_balance": _to_decimal(starting_balance),
                "ending_balance": _to_decimal(ending_balance),
                "realized_pnl": _to_decimal(realized_pnl),
                "win_count": win_count,
                "loss_count": loss_count,
                "kill_switch_triggered": kill_switch_triggered,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    def get_daily_pnl(self, date_str: str) -> Optional[dict]:
        resp = self._daily_pnl.get_item(Key={"date": date_str})
        item = resp.get("Item")
        if not item:
            return None
        return {
            "date": item["date"],
            "starting_balance": _from_decimal(item["starting_balance"]),
            "ending_balance": _from_decimal(item["ending_balance"]),
            "realized_pnl": _from_decimal(item["realized_pnl"]),
            "win_count": int(item["win_count"]),
            "loss_count": int(item["loss_count"]),
            "kill_switch_triggered": bool(item["kill_switch_triggered"]),
        }

    def get_all_daily_pnl(self) -> List[dict]:
        resp = self._daily_pnl.scan()
        return [
            {
                "date": item["date"],
                "starting_balance": _from_decimal(item["starting_balance"]),
                "ending_balance": _from_decimal(item["ending_balance"]),
                "realized_pnl": _from_decimal(item["realized_pnl"]),
                "win_count": int(item["win_count"]),
                "loss_count": int(item["loss_count"]),
            }
            for item in resp.get("Items", [])
        ]


# ---------------------------------------------------------------------------
# Standalone: create tables
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)

    db = DynamoClient()
    db.ensure_tables_exist()
    print("Tables created/verified. DynamoDB is ready.")

    # Smoke test
    db.put_calibration("LA", "2026-02-19", "19", 68.5, 4.2)
    history = db.get_calibration_history("LA", lookback_days=7)
    print(f"Calibration history for LA (last 7 days): {len(history)} records with actuals")
    print("DynamoDB smoke test passed.")
