# Automatic listing photo completion

The existing source completion schedule also processes missing listing photos.
The photo queue uses the saved completion start date, active status, price watch
scope, unique identity and supported counties (Allegheny and Erie). It does not
inherit the Tarasa specification collector's Allegheny-only request restriction.

The Preferred Realty public MLS route is attempted first. Acceptance requires
the current MLS in the canonical URL and primary description, the full street,
postal city, state and ZIP in the primary title, and the subject's image alt text
inside the listing gallery. Only this provider's explicit parenthesized
neighborhood suffix is removed from its postal city before comparison.

Published Cotality media URLs are read from the verified gallery, never guessed.
The reader checks robots rules, image MIME and magic bytes, size, and SHA-256.
Photo receipts retain the listing identity, source URL, verification and date.
The frontend requires the same current binding and allowed media path, and
counts images only after actual browser load events. Original Redfin photos
keep priority when already available. No image bytes are stored in Git.

The new provider makes old exhausted receipts eligible again without resetting
the failed attempts of older providers. A provider version in the idle input
fingerprint wakes an idle worker after deployment. New matching inventory and
review subjects enter the same queue automatically. Existing batch, daily,
request, time and host cooldown limits remain in effect. Backfill is gradual;
missing, blocked or mismatched listings remain explicitly unavailable while
the rest of the scan continues. Provider coverage is not a promise that every
listing has publicly accessible photos.

No browser extension, local component, paid API, or credentials are required.
