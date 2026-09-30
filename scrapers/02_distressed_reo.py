"""Retired placeholder for bank-owned / REO listings.

The previous implementation generated fabricated properties with random
addresses and prices. Do not use it as a data source. The central orchestrator
currently checks the Freddie Mac HomeSteps feed; other REO providers should be
added only after a verified public feed is configured.
"""


def run_collector():
    print(
        "[Agent 02 - Distressed / REO] Disabled: no verified public feed is "
        "configured. No records were generated."
    )
    return []


if __name__ == "__main__":
    run_collector()
