from setuptools import setup, find_packages

setup(
    name="fnf_fast_converter",
    version="1.1.2",
    description="High-Speed Native Rock Band CON to Clone Hero Converter",
    packages=find_packages(),
    install_requires=[
        "numpy>=1.20",
        "soundfile>=0.12.0",
        "pillow>=9.0.0",
    ],
    entry_points={
        "console_scripts": [
            "fnf_fast_converter=fnf_fast_converter.src.cli:main",
        ],
    },
    python_requires=">=3.9",
)
