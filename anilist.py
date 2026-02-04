"""
AniList metadata lookup module.
Used to verify/lookup anime metadata (title, year) for Plex folder naming.
"""

import asyncio
import re
from dataclasses import dataclass

import aiohttp


ANILIST_API = "https://graphql.anilist.co"


def clean_for_search(name: str) -> str:
    """
    Clean anime name for AniList search by removing season info and other artifacts.
    """
    patterns = [
        r'\s*\(S\d+(?:\+S\d+)*\)',  # (S1), (S1+S2)
        r'\s*S\d{1,2}$',            # S01 at end
        r'\s*Season\s*\d+',         # Season 1, Season 2
        r'\s*Part\s*\d+',           # Part 1, Part 2
        r'\s*Cour\s*\d+',           # Cour 1, Cour 2
        r'\s*\d{1,2}(?:st|nd|rd|th)\s*Season',  # 2nd Season
        # Roman numerals at end
        r'\s+(XIII|XII|XI|IX|VIII|VII|VI|IV|III|II|X|V|I)$',
    ]

    result = name
    for pattern in patterns:
        result = re.sub(pattern, '', result, flags=re.IGNORECASE)

    return result.strip()


SEARCH_QUERY = """
query ($search: String) {
  Page(page: 1, perPage: 10) {
    media(search: $search, type: ANIME, sort: POPULARITY_DESC) {
      id
      title {
        romaji
        english
        native
      }
      format
      status
      seasonYear
      episodes
      startDate {
        year
      }
    }
  }
}
"""


@dataclass
class AnimeMetadata:
    id: int
    title_romaji: str
    title_english: str | None
    title_native: str | None
    format: str | None  # TV, MOVIE, OVA, ONA, SPECIAL, etc.
    year: int | None
    episodes: int | None

    @property
    def display_title(self) -> str:
        """Return the best title for display (prefer English)."""
        return self.title_english or self.title_romaji

    @property
    def is_movie(self) -> bool:
        return self.format == "MOVIE"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.display_title,
            "title_romaji": self.title_romaji,
            "title_english": self.title_english,
            "format": self.format,
            "year": self.year,
            "episodes": self.episodes,
            "is_movie": self.is_movie,
        }


async def search_anilist(anime_name: str) -> list[AnimeMetadata]:
    """
    Search AniList for anime matching the given name.
    Returns a list of matching anime with metadata.
    """
    search_query = clean_for_search(anime_name)

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                ANILIST_API,
                json={
                    "query": SEARCH_QUERY,
                    "variables": {"search": search_query}
                },
                headers={"Content-Type": "application/json"}
            ) as resp:
                if resp.status != 200:
                    return []

                data = await resp.json()
                media_list = data.get("data", {}).get("Page", {}).get("media", [])

                results = []
                for media in media_list:
                    title = media.get("title", {})
                    # Use seasonYear first, fallback to startDate.year
                    year = media.get("seasonYear")
                    if not year:
                        start_date = media.get("startDate", {})
                        year = start_date.get("year") if start_date else None

                    results.append(AnimeMetadata(
                        id=media.get("id"),
                        title_romaji=title.get("romaji", "Unknown"),
                        title_english=title.get("english"),
                        title_native=title.get("native"),
                        format=media.get("format"),
                        year=year,
                        episodes=media.get("episodes"),
                    ))

                return results

        except Exception as e:
            print(f"AniList search error: {e}")
            return []


async def lookup_metadata(anime_name: str) -> AnimeMetadata | None:
    """
    Look up metadata for an anime. Returns the best match or None.
    """
    results = await search_anilist(anime_name)
    return results[0] if results else None


# Test
if __name__ == "__main__":
    async def test():
        print("Testing AniList metadata lookup...")

        test_names = [
            "The Apothecary Diaries",
            "KonoSuba",
            "Frieren",
            "Made in Abyss",
            "Solo Leveling",
            "Overlord",
        ]

        for name in test_names:
            print(f"\nSearching: {name}")
            results = await search_anilist(name)
            if results:
                top = results[0]
                print(f"  Match: {top.display_title} ({top.year})")
                print(f"  Format: {top.format}, Episodes: {top.episodes}")
            else:
                print("  No results found")

    asyncio.run(test())
