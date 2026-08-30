from datetime import datetime, timezone
import unittest

from agent_workflow.detect.injections import collapse_controlled_projections
from agent_workflow.detect.scan import Signal


def signal(cell, loss):
    return Signal(cell, .5, .9, "test", 20, loss, 100, 50, .5, {}, 1000)


class ControlledInjectionTests(unittest.TestCase):
    def test_controlled_target_collapses_its_overlapping_projections(self):
        provider_country = signal({"provider": "stripe", "country": "MX"}, 20)
        country_method = signal({"country": "MX", "method": "card"}, 18)
        unrelated = signal({"provider": "adyen", "country": "BR"}, 12)
        result = collapse_controlled_projections(
            [provider_country, country_method, unrelated],
            [{"provider": "stripe", "country": "MX", "method": "card"}],
        )
        self.assertEqual([unrelated, provider_country], result)


if __name__ == "__main__":
    unittest.main()
