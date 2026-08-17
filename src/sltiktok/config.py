"""Every knob worth turning, in one place.

The stages keep their own module constants - those are the defaults and the
tests read them directly. This gathers the ones a future run might want to
change (different keywords, a cheaper model, a shorter highlight list) so a
notebook can override them without editing five files.

    from sltiktok.config import Config
    cfg = Config(target_accounts=50, model="gemini-3.7-flash")
    cfg.keywords = ["ลาออกเซเว่น", "พนักงานเซเว่น"]

Nothing here is read implicitly. A stage uses a value only when the notebook
passes it in, so importing this module cannot change how the CLI behaves.
"""
from dataclasses import dataclass, field, replace

from . import analyze as _analyze
from . import dashboard as _dashboard
from . import discover as _discover
from . import enrich as _enrich
from . import ocr as _ocr


@dataclass
class Config:
    """Tunables for one pipeline run.

    Defaults are lifted from the stage modules rather than retyped, so this
    cannot drift out of sync with what the CLI actually does.
    """

    # --- discover: what to search for -----------------------------------
    # Coverage comes from many distinct queries; TikTok caps search at ~30-40
    # results however far you scroll, so adding keywords beats scrolling more.
    keywords: list[str] = field(
        default_factory=lambda: list(_discover.KEYWORDS))
    target_accounts: int = _discover.TARGET
    scrolls_per_keyword: int = 25
    headless: bool = True

    # --- enrich: how much to pull from Apify ----------------------------
    # The stage has two paths and they use different fields.
    #
    #   full run  (default)          walks each account's feed, then filters
    #   --comments-from-seed         skips the feed; takes the video_url the
    #                                sheet already carries, one clip per account
    #
    # The 99 clips behind the current dashboard came the second way: the
    # profile actor was down platform-wide, and the sheet already had a clip
    # per account. So videos_per_profile and profile_batch did nothing in that
    # run - they apply when the profile scraper is used.

    # Newest N clips per account, and how many accounts per actor run.
    # Full run only.
    videos_per_profile: int = _enrich.VIDEOS_PER_PROFILE
    profile_batch: int = _enrich.PROFILE_BATCH

    # Both paths. Comments are ~95% of the bill, so max_videos_to_comment is
    # the real budget lever; worst case ~$28 against the account's $29 cap.
    comments_per_video: int = _enrich.COMMENTS_PER_VIDEO
    max_videos_to_comment: int = _enrich.MAX_VIDEOS_TO_COMMENT

    # --- analyze: Gemini ------------------------------------------------
    model: str = _analyze.MODEL
    comment_batch: int = _analyze.COMMENT_BATCH
    # Clips download an mp4 each, so they run narrower than comments.
    clip_workers: int = _analyze.CLIP_WORKERS
    comment_workers: int = _analyze.COMMENT_WORKERS
    keep_media: bool = False
    # The label sets the model must choose from. Changing these changes the
    # schema it is held to, so old results stop reconciling - rebuild from
    # scratch rather than mixing runs.
    themes: list[str] = field(default_factory=lambda: list(_analyze.THEMES))
    intents: list[str] = field(default_factory=lambda: list(_analyze.INTENTS))
    sentiments: list[str] = field(
        default_factory=lambda: list(_analyze.SENTIMENTS))

    # --- ocr: cover-frame fallback --------------------------------------
    ocr_langs: list[str] = field(default_factory=lambda: list(_ocr.LANGS))
    ocr_min_chars: int = _ocr.MIN_MEANINGFUL_CHARS

    # --- dashboard: what reaches the page -------------------------------
    highlight_count: int = _dashboard.HIGHLIGHT_COUNT
    phrase_count: int = _dashboard.PHRASE_COUNT
    # Below quote_min a quote reads as a fragment on a card; widening tries to
    # give it back the words around it, up to quote_max.
    quote_min: int = _dashboard.QUOTE_MIN
    quote_max: int = _dashboard.QUOTE_MAX
    # Phrases to drop by hand: scaffolding, bare restatements of the subject,
    # half-sentences that survive the structural filters.
    phrase_noise: set[str] = field(
        default_factory=lambda: set(_dashboard.PHRASE_NOISE))
    # Encouragement. Real, and counted, but shown apart from the topics an
    # executive can act on.
    support_phrases: set[str] = field(
        default_factory=lambda: set(_dashboard.SUPPORT_PHRASES))

    def with_(self, **kw):
        """A copy with some fields changed - keeps a cell from mutating state.

            small = cfg.with_(target_accounts=20, phrase_count=10)
        """
        return replace(self, **kw)

    def summary(self):
        """One line per stage, for a notebook cell to print before a run."""
        return "\n".join([
            f"discover   {len(self.keywords)} keywords, target "
            f"{self.target_accounts} accounts, {self.scrolls_per_keyword} scrolls",
            f"enrich     {self.comments_per_video} comments/video, "
            f"cap {self.max_videos_to_comment} videos"
            f"  (full run only: {self.videos_per_profile} videos/profile)",
            f"analyze    {self.model}, batch {self.comment_batch}, "
            f"workers {self.clip_workers} clip / {self.comment_workers} comment",
            f"ocr        {'+'.join(self.ocr_langs)}, "
            f"min {self.ocr_min_chars} chars",
            f"dashboard  {self.highlight_count} highlights, "
            f"{self.phrase_count} phrases, quote {self.quote_min}-{self.quote_max}",
        ])
