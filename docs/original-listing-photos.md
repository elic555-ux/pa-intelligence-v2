# Original listing photo recovery

The stored Tarasa image URLs for 8060 Aber Rd (current MLS 1779861) return Cloudflare block pages in the browser. The original Redfin listing still publishes usable photographs. The source page's address and current MLS were checked, and three actual URLs from its subject gallery loaded both on Redfin and on a separate origin with no referrer. No image filenames were guessed and no image bytes are copied into the repository.

The original-source snapshot is photo-only: it does not replace inventory or financial facts. Its cache key binds the normalized address, city, state, ZIP, original Redfin home URL and current MLS.

The shared browser gallery now prioritizes saved, identity-bound original Redfin photos before secondary candidates. Original and secondary snapshots may finish in either order; late original photos rebuild the gallery. A failed original can still advance to an independently verified backup. The separate primary gallery is hidden only while the shared gallery owns those photos, avoiding duplicates and contradictory failure messages. Load counts continue to reflect browser load events.

Validation: 4 photo provenance tests and 48 real-modal integration checks pass offline, including late original arrival, original-to-backup fallback, mismatch rejection and preservation of existing fields. The existing full regression workflow must pass before deployment, then the live gallery must show three loaded photos. Other properties without saved usable original URLs still depend on their available sources; this change does not claim to remove external provider blocks.
