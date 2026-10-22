"""Retrieve and store a live token without starting trading or printing secrets."""

import argparse

from app.services.upstox_totp import retrieve_token


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--connect",
        action="store_true",
        help="Allow initial connection when no saved live token exists",
    )
    args = parser.parse_args(argv)
    try:
        result = retrieve_token(connect=args.connect)
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 -- never print broker/DB exceptions or secrets
        print(
            "Token retrieval failed. Check PIN/TOTP configuration, system clock, credential-operation availability, broker and MySQL availability. A previous token may have been revoked; no automatic retry was made."
        )
        return 2
    messages = {
        "saved": "Verified live token saved in MySQL. No trading was started.",
        "valid": "Existing live token is valid; no new token issued.",
        "disconnected": "Account is disconnected; automatic renewal skipped. Use --connect for an intentional connection.",
    }
    print(messages[result])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
