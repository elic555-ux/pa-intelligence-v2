"""Persist controlled report-source caches before an AI request in Actions."""
import os,subprocess

def persist_gemini_reservation(repo,message='Reserve explicit Gemini photo check before API request'):
    if os.environ.get('GITHUB_ACTIONS')!='true':raise ValueError('reservation_persistence_requires_actions')
    def git(*args,check=True):
        return subprocess.run(['git',*args],cwd=repo,check=check,capture_output=True,text=True)
    git('config','user.name','github-actions[bot]')
    git('config','user.email','41898282+github-actions[bot]@users.noreply.github.com')
    # A clean Actions checkout contains only this job's controlled source cache
    # changes; other cache writes must not prevent rebase before the reservation.
    git('add','COMPS_REPORTS')
    diff=git('diff','--cached','--quiet',check=False)
    if diff.returncode not in (0,1):raise ValueError('reservation_diff_failed')
    if diff.returncode==1:
        git('commit','-m',message)
        try:git('pull','--rebase','origin','main')
        except subprocess.SubprocessError:
            git('rebase','--abort',check=False)
            raise
        git('push','origin','HEAD:main')
