import copy
from datetime import timedelta
from pathlib import Path
import unittest
from unittest.mock import patch

import listing_photo_fallback as photo
import property_sources as registry
from test_listing_photo_fallback import ROW, FakeReader
import test_listing_photo_fallback as legacy
import test_listing_photo_queue as legacy_queue

ORIGIN = 'https://www.thepreferredrealty.com/real-estate/'
MEDIA = 'https://api.cotality.com/trestle/Media/Property/PHOTO-Jpeg/1193411277/{}/encoded/encoded/signature'


def preferred(row=ROW):
    url = ORIGIN + registry.listing_id(row) + '/'
    return ('<title>' + row['address'] + ', ' + row['city'] + ' (Beechview), PA ' + row['zip'] + ' | Real Estate</title>'
        '<link rel="canonical" href="' + url + '">'
        '<meta name="description" content="' + row['address'] + ' (MLS#:' + registry.listing_id(row) + ')">'
        '<section class="flat-gallery-single">' + ''.join('<img alt="' + row['address'] + '  - photo ' + str(n) +
        '" src="' + MEDIA.format(n) + '">' for n in range(1, 4)) + '</section>')


class PreferredReader(FakeReader):
    def page(self, url):
        if url == ORIGIN + '1001/':
            self.requests += 1
            self.urls.append(url)
            return preferred()
        return super().page(url)


class PreferredTests(unittest.TestCase):
    setUp = legacy.PhotoTests.setUp
    complete = legacy.PhotoTests.complete
    def test_preferred_identity_and_actual_gallery(self):
        url = ORIGIN + '1001/'
        self.assertEqual(photo.parse_listing(preferred(), ROW, url, 'preferredrealty'), [MEDIA.format(n) for n in range(1, 4)])
        for before, after in [('12 First St,', '14 First St,'), ('Pittsburgh (Beechview)', 'Erie (Beechview)'),
                              ('15216', '16504'), ('MLS#:1001', 'MLS#:9999'), ('href="'+url, 'href="'+ORIGIN+'9999/'),
                              ('12 First St,', '12 First St #2,')]:
            with self.subTest(after=after), self.assertRaises(photo.Unavailable):
                photo.parse_listing(preferred().replace(before, after), ROW, url, 'preferredrealty')
        html = preferred().replace('</section>', '<img alt="14 First St - photo 4" src="'+MEDIA.format(4)+'"></section>')
        self.assertEqual(len(photo.parse_listing(html, ROW, url, 'preferredrealty')), 3)
        self.assertEqual(photo.parse_listing(preferred().replace('12 First St  - photo', '14 First St - photo'), ROW, url, 'preferredrealty'), [])

    def test_published_media_path_is_narrowly_allowed(self):
        valid = MEDIA.format(1)
        self.assertEqual(photo.safe_url(valid, image=True), valid)
        for invalid in [valid+'?token=x', valid+'#x', valid.replace('api.', 'api.evil.'),
                        valid.replace('/trestle/Media/Property/PHOTO-Jpeg/', '/anything/'),
                        'https://api.cotality.com/a.jpg', valid.replace('/1193411277/', '/../')]:
            self.assertIsNone(photo.safe_url(invalid, image=True))

    def test_generic_route_wins_without_contacting_blocked_old_sources(self):
        reader = PreferredReader()
        record = self.complete(reader)
        self.assertEqual([a['provider'] for a in record['attempts']], ['preferredrealty'])
        self.assertEqual(len(record['photos']), 3)
        self.assertEqual(reader.requests, 4)
        self.assertEqual((self.repo/'properties.json').read_bytes(), self.before)

    def test_new_provider_wakes_exhausted_receipts_without_resetting_old_limits(self):
        with patch.object(photo, 'primary_photos', return_value=(None, None, [])):
            record = self.complete(FakeReader(broken=[]))
            record['photos'] = []
            record['attempts'] = [{'provider': p, 'unsuccessful_attempts': 2} for p in ('saved_source','coldwellbanker','weichert')]
            registry.atomic_json(self.repo/photo.ROOT/(photo.core.cache_key(ROW['id'])+'.json'), record)
            due, pending, _ = photo.queue(self.repo, {ROW['id']: ROW}, self.now)
            self.assertEqual(due, [ROW['id']])
            result = photo.complete(self.repo, ROW, PreferredReader(), record, self.now)
            self.assertEqual(len(result['photos']), 3)


class PreferredQueueTests(unittest.TestCase):
    setUp = legacy_queue.PhotoQueueTests.setUp
    save = legacy_queue.PhotoQueueTests.save
    def test_photo_scope_includes_erie_and_keeps_configured_filters(self):
        erie = {**self.rows[0], 'id':'PA-MLS-1002', 'docket_id':'MLS-1002', 'county':'Erie', 'city':'Erie', 'zip':'16504'}
        self.rows += [erie, {**erie, 'id':'PA-MLS-1003', 'listed_date':'2026-01-01'},
                     {**erie, 'id':'PA-MLS-1004', 'market_status':'sold'}]
        self.save()
        specs, _ = photo_worker().eligible_rows(self.repo, self.now.date()-timedelta(days=30), self.now.date())
        photos, _ = photo_worker().eligible_rows(self.repo, self.now.date()-timedelta(days=30), self.now.date(), photo_scope=True)
        self.assertNotIn(erie['id'], specs)
        self.assertEqual(set(photos), {ROW['id'], erie['id']})


def photo_worker():
    import automatic_listing_sources
    return automatic_listing_sources
