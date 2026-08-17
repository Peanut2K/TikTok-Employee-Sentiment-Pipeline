"""TikTok 7-Eleven staff-voice pipeline.

Five stages, each runnable on its own and each picking up where the last
left off through files in out/:

    discover   Playwright search -> accounts + video_url (Google Sheet)
    enrich     Apify -> engagement counts, uploaded_at, raw comments
    analyze    Gemini -> transcript, on-screen text, themes, intent
    ocr        easyocr on the cover frame, used only where analyze failed
    dashboard  aggregates + pythainlp phrases -> web/data.js
"""
