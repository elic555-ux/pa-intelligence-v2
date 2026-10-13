"""Browser smoke checks for the generated preview. All remote requests are stubbed."""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context()
    context.add_init_script("if(!sessionStorage.getItem('preview-test-setup')){localStorage.setItem('pa_github_token','production-test-token');localStorage.setItem('unrelated','keep');sessionStorage.setItem('preview-test-setup','yes');}")
    remote = []
    def route(request):
        url = request.request.url
        if url.startswith("http://127.0.0.1:8765/"):
            request.continue_()
            return
        remote.append(url)
        if "lucide" in url:
            request.fulfill(body="window.lucide={createIcons(){}};", content_type="application/javascript")
        elif "tailwind" in url:
            request.fulfill(body="var s=document.createElement('style');s.textContent='.hidden{display:none!important}';document.head.appendChild(s);", content_type="application/javascript")
        elif request.request.resource_type == "script":
            request.fulfill(body="", content_type="application/javascript")
        else:
            request.fulfill(body="", content_type="text/plain")
    context.route("**/*", route)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://127.0.0.1:8765/preview/index.html")
    page.wait_for_function("typeof rawProperties !== 'undefined' && rawProperties.length === 3 && !document.getElementById('pa-app-shell').hidden")
    assert page.locator("#pa-preview-banner").is_visible()
    assert "סביבת בדיקה" in page.locator("#pa-preview-banner").inner_text()
    page.evaluate("openPropertyModal('PA-MLS-1778804')")
    page.wait_for_function("document.getElementById('modal-additional-source-notice')?.textContent.includes('Tarasa')")
    assert page.locator("#modal-listing-source-request").count() == 0
    assert page.locator("#modal-additional-source-request").count() == 0
    assert page.locator("#modal-additional-source-photos img").count() == 3
    page.evaluate("closePropertyModal()")
    result = page.evaluate("""async()=>{const client=window.supabase.createClient();await client.from('pipeline_deals').upsert({property_id:'test',deal_data:{id:'test',address:'Preview'}});return(await client.from('pipeline_deals').select('*')).data.length;}""")
    assert result == 1
    assert not any("supabase.co" in url or "api.rentcast.io" in url or "api.github.com" in url for url in remote), remote
    assert not errors, errors
    page.evaluate("localStorage.clear()")
    page.goto("http://127.0.0.1:8765/index.html")
    assert page.evaluate("localStorage.getItem('pa_github_token')") == "production-test-token"
    assert page.evaluate("localStorage.getItem('unrelated')") == "keep"
    browser.close()
    print("PASS browser preview, example modal, automatic source display, local persistence and no production network")
