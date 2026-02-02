"""
Image fetching module using AniList API.
"""

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

import aiohttp


ANILIST_API = "https://graphql.anilist.co"


def clean_for_search(name: str) -> str:
    """
    Clean anime name for AniList search by removing season info and other artifacts.
    """
    # Remove season patterns
    patterns = [
        r'\s*\(S\d+(?:\+S\d+)*\)',  # (S1), (S1+S2)
        r'\s*S\d{1,2}$',            # S01 at end
        r'\s*Season\s*\d+',         # Season 1, Season 2
        r'\s*Part\s*\d+',           # Part 1, Part 2
        r'\s*Cour\s*\d+',           # Cour 1, Cour 2
        r'\s*\d{1,2}(?:st|nd|rd|th)\s*Season',  # 2nd Season
    ]

    result = name
    for pattern in patterns:
        result = re.sub(pattern, '', result, flags=re.IGNORECASE)

    return result.strip()

# GraphQL query for searching anime
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
      coverImage {
        extraLarge
        large
        medium
      }
      bannerImage
      format
      status
      seasonYear
      episodes
    }
  }
}
"""


@dataclass
class AnimeResult:
    id: int
    title_romaji: str
    title_english: str | None
    title_native: str | None
    cover_large: str | None
    cover_extra_large: str | None
    banner: str | None
    format: str | None
    year: int | None
    episodes: int | None

    @property
    def best_cover(self) -> str | None:
        """Return the highest quality cover available."""
        return self.cover_extra_large or self.cover_large

    @property
    def display_title(self) -> str:
        """Return the best title for display."""
        return self.title_english or self.title_romaji

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.display_title,
            "title_romaji": self.title_romaji,
            "title_english": self.title_english,
            "cover_url": self.best_cover,
            "banner_url": self.banner,
            "format": self.format,
            "year": self.year,
            "episodes": self.episodes,
        }


async def search_anilist(anime_name: str) -> list[AnimeResult]:
    """
    Search AniList for anime matching the given name.
    Returns a list of matching anime with cover images.
    """
    # Clean the name for better search results
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
                    cover = media.get("coverImage", {})
                    title = media.get("title", {})

                    results.append(AnimeResult(
                        id=media.get("id"),
                        title_romaji=title.get("romaji", "Unknown"),
                        title_english=title.get("english"),
                        title_native=title.get("native"),
                        cover_large=cover.get("large"),
                        cover_extra_large=cover.get("extraLarge"),
                        banner=media.get("bannerImage"),
                        format=media.get("format"),
                        year=media.get("seasonYear"),
                        episodes=media.get("episodes"),
                    ))

                return results

        except Exception as e:
            print(f"AniList search error: {e}")
            return []


async def download_image(url: str, save_path: str, also_save_as: str | None = None) -> bool:
    """
    Download an image from URL and save it to the specified path.
    Optionally saves a copy to also_save_as (for Plex poster.jpg compatibility).
    Returns True on success.
    """
    save_path = Path(save_path)

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return False

                # Read image data once
                image_data = await resp.read()

                # Ensure parent directory exists
                save_path.parent.mkdir(parents=True, exist_ok=True)

                # Write the image
                with open(save_path, 'wb') as f:
                    f.write(image_data)

                # Also save as poster.jpg if requested
                if also_save_as:
                    also_path = Path(also_save_as)
                    with open(also_path, 'wb') as f:
                        f.write(image_data)

                # Also save as folder.jpg (another Plex Local Media Assets convention)
                folder_jpg = save_path.parent / "folder.jpg"
                with open(folder_jpg, 'wb') as f:
                    f.write(image_data)

                return True

        except Exception as e:
            print(f"Image download error: {e}")
            return False


async def fetch_and_save_cover(anime_name: str, folder_path: str) -> tuple[bool, str | None]:
    """
    Search for anime on AniList and save the first result's cover image.
    Returns (success, cover_path).
    """
    results = await search_anilist(anime_name)
    if not results:
        return False, None

    # Use the first (most popular) result
    best_match = results[0]
    cover_url = best_match.best_cover

    if not cover_url:
        return False, None

    cover_path = Path(folder_path) / "cover.jpg"
    success = await download_image(cover_url, str(cover_path))

    return success, str(cover_path) if success else None


# Test
if __name__ == "__main__":
    async def test():
        print("Testing AniList search...")

        test_names = [
            "The Apothecary Diaries",
            "KonoSuba",
            "Frieren",
            "Made in Abyss",
            "Solo Leveling",
        ]

        for name in test_names:
            print(f"\nSearching: {name}")
            results = await search_anilist(name)
            if results:
                top = results[0]
                print(f"  Top result: {top.display_title} ({top.year})")
                print(f"  Cover: {top.best_cover}")
            else:
                print("  No results found")

    asyncio.run(test())
