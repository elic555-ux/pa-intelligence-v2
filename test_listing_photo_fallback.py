import copy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import listing_photo_fallback as photo
import property_sources as registry
import secondary_listing_source as core

ROW = {'id': 'PA-MLS-1001', 'docket_id': 'MLS-1001', 'source_type': 'mls',
       'address': '12 First St', 'city': 'Pittsburgh', 'state': 'PA',
       'county': 'Allegheny', 'zip': '15216',
       'url': 'https://www.redfin.com/PA/Pittsburgh/12-First-St/home/1'}
CB = 'https://www.coldwellbankerhomes.com/pa/pittsburgh/12-first-st/pid_12345/'
WC = 'https://www.weichert.com/12345/'
IMAGES = ['https://m1.cbhomes.com/p/716/1001/image' + str(n) + '.jpg' for n in range(1, 4)]


def listing(images=None, row=None, provider='coldwellbanker'):
    row = row or ROW
    data = {'@type': 'SingleFamilyResidence', 'address': {'streetAddress': row['address'],
        'addressLocality': row['city'], 'addressRegion': row['state'], 'postalCode': row['zip']},
        'image': images if images is not None else IMAGES}
    return '<html><title>' + row['address'] + ', ' + row['city'] + ', PA ' + row['zip'] + \
        ' - MLS #1001</title><h1>' + row['address'] + '</h1><script type="application/ld+json">' + \
        json.dumps(data) + '</script></html>'


class FakeReader:
    def __init__(self, broken=()):
        self.requests = 0
        self.broken = broken
        self.urls = []
    def page(self, url):
        self.requests += 1
        self.urls.append(url)
        if url.endswith('/pa/pittsburgh/'):
            return '<a href="' + CB + '">12 First St</a>'
        if url.endswith('/PA/Allegheny/Pittsburgh/'):
            return '<a href="' + WC + '"><img alt="12 First St MLS#1001"></a>'
        if url == CB:
            return listing()
        if url == WC:
            return listing(['https://d36xftgacqn2p.cloudfront.net/p/1001/a.jpg'])
        raise photo.Unavailable('http_error', 404)
    def image(self, url):
        self.requests += 1
        self.urls.append(url)
        if url in self.broken:
            raise photo.Unavailable('http_error', 404)
        return {'content_type': 'image/jpeg', 'bytes': 200, 'sha256': 'a' * 64}


class PhotoTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 18, 13, tzinfo=timezone.utc)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        registry.atomic_json(self.repo / 'properties.json', [ROW])
        self.before = (self.repo / 'properties.json').read_bytes()

    def complete(self, reader=None, previous=None, primary=None):
        with patch.object(photo, 'primary_photos', return_value=primary or (None, None, [])):
            return photo.complete(self.repo, ROW, reader or FakeReader(), previous, self.now)

    def test_full_address_mls_and_source_bind_before_photos(self):
        self.assertEqual(photo.parse_listing(listing(), ROW, CB, 'coldwellbanker'), IMAGES)
        for field, value in [('address', '14 First St'), ('city', 'Erie'), ('zip', '16504'),
                             ('address', '12 First St #2'), ('address', '12 1/2 First St')]:
            other = {**ROW, field: value}
            with self.assertRaises(photo.Unavailable):
                photo.parse_listing(listing(row=other), ROW, CB, 'coldwellbanker')
        with self.assertRaises(photo.Unavailable):
            photo.parse_listing(listing().replace('MLS #1001', 'MLS #9999'), ROW, CB, 'coldwellbanker')

    def test_address_suffix_alias_and_unit_are_checked(self):
        self.assertEqual(photo.parse_listing(listing(row={**ROW, 'address': '12 First Street'}), ROW, CB, 'coldwellbanker'), IMAGES)
        self.assertFalse(photo.address_match({**ROW, 'address': '12 First St #2'}, '12 First St', 'Pittsburgh', 'PA', '15216'))

    def test_hidden_address_is_not_reconstructed_from_url(self):
        html = listing().replace('<title>', '<title>Address not provided ')
        with self.assertRaisesRegex(photo.Unavailable, 'identity_hidden'):
            photo.parse_listing(html, ROW, CB, 'coldwellbanker')

    def test_only_current_listing_images_not_neighbors(self):
        html = listing([]).replace('</html>',
            '<img alt="14 First St, Pittsburgh, PA 15216" src="' + IMAGES[0] + '">' +
            '<img alt="12 First Street, Pittsburgh, PA 15216 - Photo 1" src="' + IMAGES[1] + '"></html>')
        self.assertEqual(photo.parse_listing(html, ROW, CB, 'coldwellbanker'), [IMAGES[1]])

    def test_untrusted_hosts_credentials_redirect_queries_rejected(self):
        for value in ['http://m1.cbhomes.com/a.jpg', 'https://m1.cbhomes.com.evil.test/a.jpg',
                      'https://user:secret@m1.cbhomes.com/a.jpg', IMAGES[0] + '?auth=x',
                      'https://127.0.0.1/a.jpg', 'https://m1.cbhomes.com:444/a.jpg',
                      'https://m1.cbhomes.com/a.svg']:
            self.assertIsNone(photo.safe_url(value, image=True))
        with self.assertRaises(photo.Unavailable):
            photo.parse_listing(listing(), ROW, CB.replace('.com', '.com.evil.test'), 'coldwellbanker')

    def test_image_bytes_and_mime_both_required(self):
        self.assertEqual(photo.image_type(b'\xff\xd8\xffmore', 'image/jpeg'), 'image/jpeg')
        self.assertEqual(photo.image_type(b'RIFFxxxxWEBPmore', 'image/webp'), 'image/webp')
        self.assertIsNone(photo.image_type(b'<html>denied</html>', 'image/jpeg'))
        self.assertIsNone(photo.image_type(b'\xff\xd8\xffmore', 'text/html'))

    def test_broken_primary_tries_next_source_without_touching_inventory(self):
        broken = 'https://cdn.listingphotos.sierrastatic.com/pics3x/v1/56/56_1001_01.jpg'
        r = self.complete(FakeReader(broken=[broken]), primary=('https://www.tarasa.com/a', 'Tarasa', [broken]))
        self.assertEqual(len(r['photos']), 3)
        self.assertEqual([a['provider'] for a in r['attempts']], ['saved_source', 'coldwellbanker'])
        self.assertEqual(r['photos'][0]['verification']['status'], 'image_verified')
        self.assertEqual((self.repo / 'properties.json').read_bytes(), self.before)
        self.assertEqual(r['new_rentcast_calls'], 0)

    def test_blocked_provider_moves_to_another_without_repeating_block(self):
        class Blocked(FakeReader):
            def page(self, url):
                if 'coldwellbankerhomes' in url:
                    self.requests += 1
                    raise photo.Unavailable('blocked', 403)
                return super().page(url)
        r = self.complete(Blocked())
        self.assertEqual(r['photos'][0]['provider'], 'weichert')
        self.assertEqual(r['attempts'][1]['status'], 'blocked')

    def test_missing_images_is_completion_not_scan_failure(self):
        r = self.complete(FakeReader(broken=IMAGES + ['https://d36xftgacqn2p.cloudfront.net/p/1001/a.jpg']))
        self.assertEqual(r['status'], 'not_completed')
        self.assertEqual(len(r['attempts']), 3)
        self.assertNotIn('scanner_status', r)

    def test_two_unsuccessful_attempts_then_no_further_requests(self):
        reader = FakeReader(broken=IMAGES + ['https://d36xftgacqn2p.cloudfront.net/p/1001/a.jpg'])
        first = self.complete(reader)
        later = self.now + timedelta(days=2)
        with patch.object(photo, 'primary_photos', return_value=(None, None, [])):
            second = photo.complete(self.repo, ROW, reader, first, later)
            count = reader.requests
            third = photo.complete(self.repo, ROW, reader, second, later + timedelta(days=2))
        self.assertEqual(reader.requests, count)
        self.assertTrue(third['sources_exhausted'])

    def test_request_budget_does_not_count_as_unsuccessful_source_attempt(self):
        class Budget(FakeReader):
            def page(self, url):
                raise photo.Unavailable('request_budget')
        r = self.complete(Budget())
        self.assertEqual(r['attempts'][-1]['unsuccessful_attempts'], 0)
        self.assertEqual(r['attempts'][-1]['status'], 'request_budget')

    def test_fresh_verified_cache_avoids_network(self):
        r = self.complete()
        reader = FakeReader()
        again = self.complete(reader, previous=r)
        self.assertEqual(again['status'], 'cache_used')
        self.assertEqual(reader.requests, 0)

    def test_changed_inventory_identity_cannot_reuse_old_photos(self):
        r = self.complete()
        self.assertFalse(photo.same_binding({**ROW, 'url': ROW['url'] + 'changed'}, r))
        r['identity'][0] = '14 FIRST ST'
        self.assertFalse(photo.same_binding(ROW, r))

    def test_save_writes_photo_receipt_only_and_default_trial_is_read_only(self):
        with patch.object(photo, 'primary_photos', return_value=(None, None, [])):
            photo.run(self.repo, [ROW['id']], reader=FakeReader(), now=self.now)
            self.assertFalse((self.repo / photo.ROOT).exists())
            r = photo.run(self.repo, [ROW['id']], reader=FakeReader(), now=self.now, save=True)
        self.assertTrue((self.repo / photo.ROOT / (core.cache_key(ROW['id']) + '.json')).exists())
        self.assertEqual((self.repo / 'properties.json').read_bytes(), self.before)
        self.assertEqual(r['new_rentcast_calls'], 0)

    def test_duplicate_and_incomplete_property_never_requested(self):
        registry.atomic_json(self.repo / 'properties.json', [ROW, ROW])
        reader = FakeReader()
        r = photo.run(self.repo, [ROW['id']], reader=reader)
        self.assertEqual(reader.requests, 0)
        self.assertEqual(r['results'][0]['status'], 'unsupported_or_ambiguous')


class ReaderTests(unittest.TestCase):
    class Response(io.BytesIO):
        status = 200
        def __init__(self, body, mime):
            super().__init__(body)
            self.headers = {'Content-Type': mime}

    def reader(self, robot=b'User-agent: *\nAllow: /\n', image=b'\xff\xd8\xff' + b'x' * 200,
               mime='image/jpeg', robot_status=None):
        calls, now = [], [0]
        outer = self
        class Opener:
            def open(self, request, timeout):
                calls.append((request.full_url, now[0]))
                if request.full_url.endswith('/robots.txt'):
                    if robot_status:
                        raise HTTPError(request.full_url, robot_status, 'robots', {}, None)
                    return outer.Response(robot, 'text/plain')
                return outer.Response(image, mime)
        reader = photo.Reader(clock=lambda: now[0], sleep=lambda n: now.__setitem__(0, now[0] + n), opener=Opener())
        return reader, calls

    def test_robots_and_image_requests_respect_spacing(self):
        reader, calls = self.reader()
        reader.image(IMAGES[0]);reader.image(IMAGES[1])
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(b[1] - a[1] >= 10 for a, b in zip(calls, calls[1:])))

    def test_robots_403_and_html_stop_without_image_request(self):
        for kwargs in ({'robot_status': 403}, {'robot': b'<html>blocked</html>'},
                       {'robot': b'User-agent: *\nDisallow: /\n'}):
            reader, calls = self.reader(**kwargs)
            with self.assertRaises(photo.Unavailable):
                reader.image(IMAGES[0])
            self.assertEqual(len(calls), 1)

    def test_actual_robots_404_is_distinct_from_forbidden(self):
        reader, calls = self.reader(robot_status=404)
        self.assertEqual(reader.image(IMAGES[0])['content_type'], 'image/jpeg')
        self.assertEqual(len(calls), 2)

    def test_http_200_html_is_never_a_verified_photo(self):
        reader, _ = self.reader(image=b'<html>not a photo</html>', mime='text/html')
        with self.assertRaisesRegex(photo.Unavailable, 'not_image'):
            reader.image(IMAGES[0])

    def test_redirect_is_not_followed(self):
        with self.assertRaises(photo.Unavailable):
            photo.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.test/')

    def test_caps_apply_to_robots_and_images(self):
        reader, calls = self.reader()
        reader.max_requests = 1
        with self.assertRaisesRegex(photo.Unavailable, 'request_budget'):
            reader.image(IMAGES[0])
        self.assertEqual(len(calls), 1)
        reader, calls = self.reader();reader.deadline = 1
        with self.assertRaisesRegex(photo.Unavailable, 'time_budget'):
            reader.image(IMAGES[0])
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
