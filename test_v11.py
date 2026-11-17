# -*- coding: utf-8 -*-
import unittest
from types import SimpleNamespace as NS
import analyzer as a


def hs(title, summ=""):
    return a.detect_hard_signals(NS(title=title, summary=summ))[0]


class HardSignalFix(unittest.TestCase):
    def test_foldable_phone_not_hard(self):
        self.assertFalse(hs("Xu hướng trên thị trường điện thoại gập toàn cầu",
                            "Dự kiến 20 triệu thiết bị gập được bán ra"))

    def test_settlement_not_hard(self):
        self.assertFalse(hs("AT&T $177 mn data breach settlement: Who qualifies for payouts?"))

    def test_real_breach_still_hard(self):
        self.assertTrue(hs("Maileva data breach exposes information of 22 million people"))
        self.assertTrue(hs("Telegram bot leaks personal data of 50 million citizens"))
        self.assertTrue(hs("Hackers leak 5 million customer records"))
        self.assertTrue(hs("Binance hack: $3.6bn withdrawn after breach"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
