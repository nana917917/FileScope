"""UI state: current search form, refine, facets, presets, history, confirmed."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..config import Settings
from ..core.coordinator import SearchConfig
from ..core.paths import normalized_key


@dataclass
class UiState:
    """Everything the search form holds; persisted on exit (spec section 87)."""

    root: str = ""
    query: str = ""
    exclude_query: str = ""
    mode: str = "standard"
    legacy_operator: str = "OR"
    include_subfolders: bool = True
    include_excel: bool = True
    include_pdf: bool = True
    include_word: bool = True
    include_powerpoint: bool = True
    include_text: bool = True
    include_unknown_text: bool = True
    include_archives: bool = False
    search_path_names: bool = True
    search_formula: bool = True
    pdf_ocr_mode: str = "auto"
    ocr_languages: str = "jpn+eng"
    online_files_policy: str = "auto"
    case_sensitive: bool = False
    ignore_width: bool = True
    part_number_mode: bool = False
    workers: int = 4
    sort_column: str = "relevance"
    sort_descending: bool = True
    confirmed_only: bool = False
    unconfirmed_only: bool = False
    refine_text: str = ""
    facet_kinds: set[str] = field(default_factory=set)
    facet_sources: set[str] = field(default_factory=set)
    facet_ocr: bool = False

    # ----------------------------------------------------------- persistence
    @classmethod
    def from_settings(cls, settings: Settings) -> UiState:
        defaults = settings.defaults
        return cls(
            root=defaults.root,
            query=defaults.query,
            exclude_query=defaults.exclude_query,
            mode=defaults.search_mode,
            legacy_operator=defaults.legacy_operator,
            include_subfolders=defaults.include_subfolders,
            include_excel=defaults.include_excel,
            include_pdf=defaults.include_pdf,
            include_word=defaults.include_word,
            include_powerpoint=defaults.include_powerpoint,
            include_text=defaults.include_text,
            include_unknown_text=defaults.include_unknown_text,
            include_archives=defaults.include_archives,
            search_path_names=defaults.search_path_names,
            search_formula=defaults.search_formula,
            pdf_ocr_mode=defaults.pdf_ocr_mode,
            online_files_policy=defaults.online_files_policy,
            case_sensitive=defaults.case_sensitive,
            ignore_width=defaults.ignore_width,
            part_number_mode=defaults.part_number_mode,
            workers=defaults.workers,
            sort_column=defaults.sort_column,
            sort_descending=defaults.sort_descending,
        )

    def save_to(self, settings: Settings) -> None:
        defaults = settings.defaults
        defaults.root = self.root
        defaults.query = self.query
        defaults.exclude_query = self.exclude_query
        defaults.search_mode = self.mode
        defaults.legacy_operator = self.legacy_operator
        defaults.include_subfolders = self.include_subfolders
        defaults.include_excel = self.include_excel
        defaults.include_pdf = self.include_pdf
        defaults.include_word = self.include_word
        defaults.include_powerpoint = self.include_powerpoint
        defaults.include_text = self.include_text
        defaults.include_unknown_text = self.include_unknown_text
        defaults.include_archives = self.include_archives
        defaults.search_path_names = self.search_path_names
        defaults.search_formula = self.search_formula
        defaults.pdf_ocr_mode = self.pdf_ocr_mode
        defaults.online_files_policy = self.online_files_policy
        defaults.case_sensitive = self.case_sensitive
        defaults.ignore_width = self.ignore_width
        defaults.part_number_mode = self.part_number_mode
        defaults.workers = self.workers
        defaults.sort_column = self.sort_column
        defaults.sort_descending = self.sort_descending

    # --------------------------------------------------------------- search
    def to_config(self, settings: Settings) -> SearchConfig:
        roots = self.roots()
        return SearchConfig(
            roots=roots,
            query=self.combined_query(),
            legacy_operator=self.legacy_operator,
            mode=self.mode,
            case_sensitive=self.case_sensitive,
            ignore_width=self.ignore_width,
            part_number_mode=self.part_number_mode,
            include_subfolders=self.include_subfolders,
            include_excel=self.include_excel,
            include_pdf=self.include_pdf,
            include_word=self.include_word,
            include_powerpoint=self.include_powerpoint,
            include_text=self.include_text,
            include_unknown_text=self.include_unknown_text,
            include_archives=self.include_archives,
            search_path_names=self.search_path_names,
            search_formula=self.search_formula,
            pdf_ocr_mode=self.pdf_ocr_mode,
            ocr_languages=self.ocr_languages,
            online_files_policy=self.online_files_policy,
            stage_remote_files=True,
            workers=self.workers,
            confirmed=frozenset(settings.confirmed_set()),
            limits=settings.limits,
            index_enabled=settings.index_enabled and settings.index_max_bytes > 0,
            index_max_bytes=settings.index_max_bytes,
        )

    def combined_query(self) -> str:
        """Combine the primary query, result refinement and exclusions."""
        query = self.query.strip()
        refine = self.refine_text.strip()
        exclude = self.exclude_query.strip()
        if query and refine:
            query = f"({query}) & ({refine})"
        elif refine:
            query = refine
        if not exclude:
            return query
        exclusions = " ".join(
            f"!{term}" for term in exclude.replace(",", " ").split() if term
        )
        if not query:
            return exclusions
        return f"({query}) & {exclusions}"

    def roots(self) -> tuple[str, ...]:
        """V4 allowed several search folders; V5 accepts `;` separated paths."""
        parts = [part.strip().strip('"') for part in self.root.replace("\n", ";").split(";")]
        return tuple(dict.fromkeys(part for part in parts if part))

    # ------------------------------------------------------------ results


def apply_filters(rows: list, state: UiState, confirmed: set[str]) -> list:
    """Facet + confirmed filters, applied in memory (spec sections 43, 46).

    Accepts either ``FileResult`` objects or result-list rows (anything with a
    ``result`` attribute); rows keep their confirmed flag in sync.
    """
    out: list = []
    for row in rows:
        result = getattr(row, "result", row)
        if state.facet_kinds and result.file_kind.value not in state.facet_kinds:
            continue
        if state.facet_sources and result.source_type.value not in state.facet_sources:
            continue
        if state.facet_ocr and result.ocr_pages == 0:
            continue
        is_confirmed = normalized_key(result.path) in confirmed
        if hasattr(row, "confirmed"):
            row.confirmed = is_confirmed
        if state.confirmed_only and not is_confirmed:
            continue
        if state.unconfirmed_only and is_confirmed:
            continue
        out.append(row)
    return out


def record_history(settings: Settings, state: UiState, hits: int, elapsed: float) -> None:
    settings.add_history(
        {
            "query": state.combined_query(),
            "root": state.root,
            "mode": state.mode,
            "hits": hits,
            "elapsed": round(elapsed, 3),
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    )


def preset_from_state(state: UiState, name: str) -> dict:
    """A preset stores everything the search form holds (spec section 44)."""
    return {
        "name": name,
        "root": state.root,
        "query": state.query,
        "refine_text": state.refine_text,
        "exclude_query": state.exclude_query,
        "mode": state.mode,
        "legacy_operator": state.legacy_operator,
        "case_sensitive": state.case_sensitive,
        "ignore_width": state.ignore_width,
        "part_number_mode": state.part_number_mode,
        "pdf_ocr_mode": state.pdf_ocr_mode,
        "include_excel": state.include_excel,
        "include_pdf": state.include_pdf,
        "include_word": state.include_word,
        "include_powerpoint": state.include_powerpoint,
        "include_text": state.include_text,
        "include_unknown_text": state.include_unknown_text,
        "include_archives": state.include_archives,
        "include_subfolders": state.include_subfolders,
        "search_path_names": state.search_path_names,
        "sort_column": state.sort_column,
        "sort_descending": state.sort_descending,
    }


def apply_preset(state: UiState, preset: dict) -> None:
    for key, value in preset.items():
        if key == "name":
            continue
        if hasattr(state, key):
            setattr(state, key, value)
