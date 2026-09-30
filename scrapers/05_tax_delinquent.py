"""Retired placeholder for county tax-claim listings.

The previous implementation generated random addresses, prices, debts, and
docket IDs. It did not query any county.
"""


def run_collector():
    print(
        "[Agent 05 - Tax Claim] Disabled: no verified county feed is "
        "configured. No records were generated."
    )
    return []


if __name__ == "__main__":
    run_collector()
