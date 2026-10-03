"""Retired keyword miner; deliberately performs no property mutations.

Estate/probate records must come from a verified public court feed and be
matched to a property before they can be added to the live inventory. The old
description-keyword miner made unsupported seller-motivation claims and could
rewrite unrelated tax, sheriff, and MLS records, so it is kept as a safe
compatibility entry point only.
"""


def run_probate_miner():
    print(
        "[Probate/FSBO] Disabled: no verified live case feed is connected. "
        "No properties were classified or changed."
    )
    return []


if __name__ == "__main__":
    run_probate_miner()
