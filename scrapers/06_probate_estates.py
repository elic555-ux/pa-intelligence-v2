"""Retired placeholder for probate / estate leads.

The former script used hard-coded leads and wrote directly to
properties.json and history.json. It is disabled to prevent unverified
records from entering the live inventory.
"""


def run(target_county="Allegheny", target_city=""):
    print(
        f"[Agent 06 - Probate] Disabled for {target_county}/{target_city}: "
        "no verified public feed is configured. No records were written."
    )
    return []


if __name__ == "__main__":
    import sys

    county_arg = sys.argv[1] if len(sys.argv) > 1 else "Allegheny"
    city_arg = sys.argv[2] if len(sys.argv) > 2 else ""
    run(target_county=county_arg, target_city=city_arg)
