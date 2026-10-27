from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

from .constants import DEEPSEEK_BALANCE_URL
from .models import AccountBalance, ProviderProfile, ProviderTelemetry


class BalanceError(ValueError):
    pass


def deepseek_balance_url(profile: ProviderProfile) -> str:
    """Only infer the official endpoint for the official HTTPS API host."""
    if profile.kind != "deepseek":
        return ""
    if profile.quota_url.strip():
        return profile.quota_url.strip()
    try:
        parsed = urlsplit(profile.base_url.strip())
        official = (
            parsed.scheme == "https"
            and parsed.hostname == "api.deepseek.com"
            and parsed.port in {None, 443}
            and not parsed.username and not parsed.password
            and parsed.path.rstrip("/") in {"", "/v1"}
            and not parsed.query and not parsed.fragment
        )
    except ValueError:
        return ""
    return DEEPSEEK_BALANCE_URL if official else ""


def _amount(value: Any) -> Decimal:
    try:
        if not isinstance(value, (str, int, float, Decimal)) or isinstance(value, bool):
            raise ValueError
        if len(str(value)) > 100:
            raise ValueError
        number = Decimal(str(value))
        if not number.is_finite() or abs(number) > Decimal("1e18"):
            raise ValueError
        return number
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise BalanceError("余额接口返回了无效金额，未更新余额") from exc


def parse_deepseek_balance(payload: Any) -> tuple[list[AccountBalance], bool]:
    if not isinstance(payload, dict) or not isinstance(payload.get("is_available"), bool):
        raise BalanceError("余额响应格式不匹配，未更新余额")
    rows = payload.get("balance_infos")
    if not isinstance(rows, list) or not rows:
        raise BalanceError("余额接口未返回余额数据，未更新余额")
    balances: list[AccountBalance] = []
    currencies: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise BalanceError("余额明细格式不匹配，未更新余额")
        currency = row.get("currency")
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency) or currency in currencies:
            raise BalanceError("余额币种缺失或重复，未更新余额")
        currencies.add(currency)
        balances.append(AccountBalance(
            currency,
            _amount(row.get("total_balance")),
            _amount(row.get("granted_balance")),
            _amount(row.get("topped_up_balance")),
        ))
    return balances, payload["is_available"]


def format_balance_summary(telemetry: ProviderTelemetry, *, details: bool = False) -> str:
    lines: list[str] = []
    for balance in telemetry.balances:
        lines.append(f"余额 {balance.currency} {balance.total_balance:,.2f}")
        if details:
            lines.append(
                f"赠送 {balance.granted_balance:,.2f} / 充值 {balance.topped_up_balance:,.2f}"
            )
    if not lines:
        return f"余额：{telemetry.quota_message or '尚未查询'}"
    state = "可调用" if telemetry.balance_available else "余额不足，暂不可调用"
    checked = f" · 查询于 {telemetry.balance_checked_at}" if telemetry.balance_checked_at else ""
    lines.append(state + checked)
    return "\n".join(lines)
