import copy
import io
from datetime import timedelta
from pathlib import Path
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

import listing_photo_fallback as photo
import property_sources as registry
from test_listing_photo_fallback import ROW, FakeReader
import test_listing_photo_fallback as legacy
import test_listing_photo_queue as legacy_queue

ORIGIN = 'https://www.thepreferredrealty.com/real-estate/'
MEDIA = 'https://api.cotality.com/trestle/Media/Property/PHOTO-Jpeg/1193411277/{}/encoded/encoded/signature'
KEY_MEDIA = 'https://api.cotality.com/trestle/Media/Property/2006023965177/encoded/encoded/signature'


def preferred(row=ROW):
    url = ORIGIN + registry.listing_id(row) + '/'
    return ('<title>' + row['address'] + ', ' + row['city'] + ' (Beechview), PA ' + row['zip'] + ' | Real Estate</title>'
        '<link rel="canonical" href="' + url + '">'
        '<meta name="description" content="' + row['address'] + ' (MLS#:' + registry.listing_id(row) + ')">'
        '<section class="flat-gallery-single">' + ''.join('<img alt="' + row['address'] + '  - photo ' + str(n) +
        '" src="' + MEDIA.format(n) + '">' for n in range(1, 4)) + '</section>')


class PreferredReader(FakeReader):
    def __init__(self, row=ROW):
        super().__init__()
        self.row = row
    def page(self, url):
        if url == ORIGIN + registry.listing_id(self.row) + '/':
            self.requests += 1
            self.urls.append(url)
            return preferred(self.row)
        return super().page(url)


class PreferredTests(unittest.TestCase):
    setUp = legacy.PhotoTests.setUp
    complete = legacy.PhotoTests.complete
    def test_observed_media_key_route_is_accepted_inside_the_verified_gallery(self):
        html = preferred().replace(MEDIA.format(1), KEY_MEDIA)
        self.assertEqual(photo.parse_listing(html, ROW, ORIGIN+'1001/', 'preferredrealty')[0], KEY_MEDIA)
        self.assertEqual(photo.safe_url(KEY_MEDIA, image=True), KEY_MEDIA)
        for invalid in [KEY_MEDIA+'?token=x', KEY_MEDIA.replace('/2006023965177/','/other/'),
                        KEY_MEDIA.replace('/Media/Property/','/anything/'),KEY_MEDIA.replace('api.cotality.com','evil.test')]:
            self.assertIsNone(photo.safe_url(invalid, image=True))
        with self.assertRaises(photo.Unavailable):
            photo.parse_listing(html,{**ROW,'city':'Erie'},ORIGIN+'1001/','preferredrealty')
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

    def test_only_old_empty_gallery_attempt_reopens_for_the_parser_revision(self):
        with patch.object(photo,'primary_photos',return_value=(None,None,[])):
            record=self.complete(PreferredReader())
            record['photos']=[]
            legacy_attempt={'provider':'preferredrealty','status':'no_loadable_photos','unsuccessful_attempts':2,
                'retry_after_at':(self.now+timedelta(days=1)).isoformat(),'image_failures':[]}
            record['attempts']=[legacy_attempt]+[{'provider':p,'unsuccessful_attempts':2} for p in ('saved_source','coldwellbanker','weichert')]
            registry.atomic_json(self.repo/photo.ROOT/(photo.core.cache_key(ROW['id'])+'.json'),record)
            self.assertEqual(photo.queue(self.repo,{ROW['id']:ROW},self.now)[0],[ROW['id']])
            reader=PreferredReader()
            result=photo.complete(self.repo,ROW,reader,record,self.now)
            self.assertEqual(len(result['photos']),3)
            self.assertEqual(reader.requests,4)
            self.assertEqual(result['attempts'][0]['parser_revision'],photo.PREFERRED_PARSER_REVISION)
            self.assertEqual(photo.provider_attempts(record)['coldwellbanker']['unsuccessful_attempts'],2)
            for mutate in [lambda a:a.update(parser_revision=photo.PREFERRED_PARSER_REVISION),
                           lambda a:a.update(status='identity_mismatch'),
                           lambda a:a.update(image_failures=[{'status':'blocked','http_status':403}])]:
                unchanged=copy.deepcopy(legacy_attempt);mutate(unchanged)
                self.assertEqual(photo.provider_attempts({'attempts':[unchanged]})['preferredrealty']['unsuccessful_attempts'],2)


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

    def test_erie_photo_turn_runs_even_without_a_tarasa_specification_candidate(self):
        self.rows = [{**self.rows[0], 'county':'Erie', 'city':'Erie', 'zip':'16504'}]
        self.save()
        report = photo_worker().run(self.repo, 'automatic', now=self.now, photo_reader=PreferredReader(self.rows[0]))
        self.assertEqual(report['eligible_properties'], 0)
        self.assertEqual(report['photo_eligible_properties'], 1)
        self.assertEqual(report['verified_photos'], 3)
        self.assertEqual(report['attempted_properties'], 1)
        self.assertEqual((self.repo/'properties.json').read_bytes(), self.before)


def photo_worker():
    import automatic_listing_sources
    return automatic_listing_sources


class MediaRedirectTests(unittest.TestCase):
    target = ('https://media-cdn-v2.corelogic.com/5e313ecd4c32aa4a5940/photo_1024_768.jpg'
              '?Expires=1792519200&KeyName=public-media&Signature=published-signature')

    def reader(self, target=None, second_redirect=False, deny_cdn=False, budget=13, cdn_robots_status=None, image_status=None):
        calls, clock = [], [0]
        target = target or self.target
        class Response(io.BytesIO):
            status = 200
            def __init__(self, body, mime):
                super().__init__(body)
                self.headers = {'Content-Type': mime}
        class Opener:
            def open(self, req, timeout):
                calls.append(req.full_url)
                if req.full_url.endswith('/robots.txt'):
                    if cdn_robots_status and 'corelogic.com' in req.full_url:
                        raise HTTPError(req.full_url, cdn_robots_status, 'robots', {}, None)
                    deny = deny_cdn and 'corelogic.com' in req.full_url
                    return Response(b'User-agent: *\nDisallow: /\n' if deny else b'User-agent: *\nAllow: /\n', 'text/plain')
                if req.full_url == MEDIA.format(1) or second_redirect:
                    raise photo.Unavailable('redirect_stopped',302,location=target)
                if image_status:
                    raise HTTPError(req.full_url,image_status,'image',{},None)
                return Response(b'\xff\xd8\xff'+b'x'*200,'image/jpeg')
        reader = photo.Reader(max_requests=budget, opener=Opener(), clock=lambda:clock[0],
            sleep=lambda n:clock.__setitem__(0,clock[0]+n))
        return reader, calls

    def test_one_known_media_hop_checks_robots_bytes_and_shared_budget(self):
        self.assertTrue(photo.public_media_redirect(KEY_MEDIA,self.target))
        reader, calls = self.reader()
        result = reader.image(MEDIA.format(1))
        self.assertEqual(result['content_type'], 'image/jpeg')
        self.assertEqual(reader.requests, 4)
        self.assertEqual(calls[-1], self.target)
        self.assertNotIn('Signature', str(result))
        reader, calls = self.reader(budget=3)
        with self.assertRaisesRegex(photo.Unavailable, 'request_budget'):
            reader.image(MEDIA.format(1))
        self.assertEqual(len(calls),3)

    def test_arbitrary_redirects_credentials_queries_and_more_hops_rejected(self):
        for target in [self.target.replace('corelogic.com','corelogic.com.evil.test'),
                       self.target.replace('https://','http://'),self.target.replace('https://','https://user:pass@'),
                       self.target+'&auth=x',self.target.replace('/5e313ecd4c32aa4a5940/','/../'),
                       self.target.replace('media-cdn-v2.corelogic.com','127.0.0.1')]:
            reader,calls=self.reader(target=target)
            with self.assertRaises(photo.Unavailable): reader.image(MEDIA.format(1))
            self.assertEqual(len(calls),2)
        reader,calls=self.reader(second_redirect=True)
        with self.assertRaises(photo.Unavailable): reader.image(MEDIA.format(1))
        self.assertEqual(len(calls),4)
        self.assertFalse(photo.public_media_redirect(legacy.IMAGES[0],self.target))

    def test_cdn_robots_denial_prevents_asset_request(self):
        reader,calls=self.reader(deny_cdn=True)
        with self.assertRaisesRegex(photo.Unavailable,'robots_disallowed'):reader.image(MEDIA.format(1))
        self.assertEqual(len(calls),3)

    def test_missing_cdn_robots_object_is_distinct_from_a_denied_image(self):
        reader,calls=self.reader(cdn_robots_status=403)
        self.assertEqual(reader.image(MEDIA.format(1))['content_type'],'image/jpeg')
        self.assertEqual(len(calls),4)
        self.assertNotIn(photo.MEDIA_REDIRECT_HOST,reader.blocked)
        for kwargs in [{'cdn_robots_status':503},{'image_status':403},{'image_status':429}]:
            reader,calls=self.reader(**kwargs)
            with self.assertRaises(photo.Unavailable):reader.image(MEDIA.format(1))
