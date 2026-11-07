# Profile maintenance

- Edit the project descriptions, current focus and technology lists in README.md. Keep them tied to projects that are actually being built.
- The weekly profile-stats workflow regenerates the three SVGs in stats/ from GitHub's public user and repository APIs and the year-to-date contribution calendar shown on the profile.
- The daily Git-Koi workflow creates koi/koi.gif with the official yalongwastaken/git-koi@v1 action. It stages and commits that GIF only, and only when it changes.
- Neither workflow needs a personal access token or an additional secret. Both use the repository's built-in GITHUB_TOKEN to push generated files.
- Language totals exclude forks and archived repositories. The contribution card reflects the profile-visible calendar, so GitHub account privacy settings affect what it can show.
- The public-commit total counts authored commits on public, non-fork, non-archived default branches for the current calendar year. It does not count private repositories or commits that only exist on side branches.

If GitHub changes its contribution-calendar HTML, the stats workflow will fail instead of publishing a guessed activity total. Update the parser in scripts/generate_profile_stats.py and run it locally to refresh the cards.
