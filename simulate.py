# -*- coding: utf-8 -*-
"""Mo phong offline (khong can mang). Chay: python simulate.py"""
import sys

from analyzer import score_article
from collector import Article
from hot import annotate_corroboration, hot_rule_explain
from textutils import now_vn

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

NOW = now_vn()
ROWS = [
    # (tieu de, nguon, summary)
    ("$6.3 billion Binance hack is largest crypto theft on record", "CoinDesk", ""),
    ("Binance says $6.3bn stolen in biggest crypto heist in history", "The Block", ""),
    ("Binance Suspends Deposits, Withdrawals After $6.3 Billion Hack", "BleepingComputer", ""),
    ("Binance confirms security incident, freezes withdrawals", "The Record", "Unauthorized transactions were detected."),
    ("North Korea Steals $6.36 Billion From Binance in Largest Hack in History", "The Hacker News", ""),
    ("Telegram bot leaks personal data of 50 million Vietnamese citizens", "SecurityWeek", ""),
    ("Hackers breached the US Treasury, officials confirm", "The Record", ""),
    ("Hackers drain $90 million from lending protocol", "The Block", ""),
    # khong duoc la hard
    ("Microsoft patches exploited zero-day in Windows", "BleepingComputer", ""),
    ("CISA adds exploited Cisco flaw to KEV catalog", "The Hacker News", ""),
    ("FBI arrests hacker behind botnet", "The Record", ""),
    ("Google fixes Chrome zero-day exploited in the wild", "SecurityWeek", ""),
    ("Cisco warns of attacks on ASA firewalls", "BleepingComputer", ""),
    # tin thuong ngay
    ("Critical Linux kernel flaw with CVSS 9.8 lets attackers gain root", "BleepingComputer", "CVE-2026-1234 remotely exploitable."),
    ("Chrome extensions with 134 million installs caught hijacking searches", "BleepingComputer", ""),
    ("Polymarket halts trading after crypto-draining script breach, $25M traced", "BleepingComputer", ""),
    ("Japan Airlines suffers third-party data breach exposing passenger info", "BleepingComputer", ""),
    # phai bi loai
    ("Bitcoin price rises 5% as ETF inflows grow", "CoinDesk", ""),
    ("Binance launches new staking product", "CoinDesk", ""),
    ("Coinbase stock rises after earnings", "CoinDesk", ""),
    ("Best VPN 2026 discount", "BBC Technology", ""),
]


def main():
    scored = []
    for i, (title, src, summ) in enumerate(ROWS):
        a = Article(source=src, title=title, link="https://example.test/%d" % i,
                    summary=summ, published=NOW, age_hours=2.0)
        scored.append(score_article(a, NOW))
    annotate_corroboration(scored)
    print("%-62s %-17s %5s %-7s %-6s %-5s %s" % (
        "TIEU DE", "NGUON", "SCORE", "SPECIAL", "HARD", "CORR", "RULE_HOT"))
    for s in scored:
        ok, _ = hot_rule_explain(s)
        print("%-62s %-17s %5d %-7s %-6s %-5d %s" % (
            s.title[:61], s.source[:16], s.score, s.is_special,
            getattr(s, "hard_signal", False), s.corroboration, ok))


if __name__ == "__main__":
    main()
