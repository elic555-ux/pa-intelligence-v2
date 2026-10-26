import os,subprocess,unittest
from types import SimpleNamespace
from unittest.mock import patch
import report_provider_reservation as reservation

class ReservationTests(unittest.TestCase):
    def test_no_repository_write_outside_actions(self):
        with patch.dict(os.environ,{'GITHUB_ACTIONS':'false'}),patch.object(reservation.subprocess,'run',side_effect=AssertionError('git called')):
            with self.assertRaises(ValueError):reservation.persist_gemini_reservation('offline')
    def test_all_job_source_cache_changes_are_persisted_before_return(self):
        def run(args,**kwargs):return SimpleNamespace(returncode=1 if args[1:4]==['diff','--cached','--quiet'] else 0)
        with patch.dict(os.environ,{'GITHUB_ACTIONS':'true'}),patch.object(reservation.subprocess,'run',side_effect=run) as command:
            reservation.persist_gemini_reservation('offline')
        calls=[x.args[0] for x in command.call_args_list]
        self.assertIn(['git','add','COMPS_REPORTS'],calls)
        self.assertEqual(calls[-1],['git','push','origin','HEAD:main'])
    def test_failed_push_is_not_reported_as_durable_reservation(self):
        def run(args,**kwargs):
            if args[1]=='push':raise subprocess.CalledProcessError(1,args)
            return SimpleNamespace(returncode=1 if args[1:4]==['diff','--cached','--quiet'] else 0)
        with patch.dict(os.environ,{'GITHUB_ACTIONS':'true'}),patch.object(reservation.subprocess,'run',side_effect=run):
            with self.assertRaises(subprocess.CalledProcessError):reservation.persist_gemini_reservation('offline')

if __name__=='__main__':unittest.main()
