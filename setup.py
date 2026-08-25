"""Legacy packaging fallback for distributions with pre-PEP 621 setuptools."""

from pathlib import Path

from setuptools import find_packages, setup


ROOT = Path(__file__).parent

setup(
    name="scientific-agent",
    version="0.1.0",
    description="Local literature intake, research planning, and writing workspace",
    long_description=(ROOT / "README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    package_dir={"": "src"},
    packages=find_packages("src"),
    python_requires=">=3.10",
    install_requires=["PyYAML>=6.0", "langgraph>=0.2.0"],
    entry_points={"console_scripts": ["scientific-agent=literature_agent.cli:main"]},
)
