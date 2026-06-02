from setuptools import find_packages, setup


setup(
    name="agent-test-impact",
    version="0.1.0",
    description="Map coding-agent diffs to likely test coverage gaps.",
    packages=find_packages("src"),
    package_dir={"": "src"},
    python_requires=">=3.9",
    entry_points={
        "console_scripts": [
            "agent-test-impact=agent_test_impact.cli:main",
        ],
    },
)
