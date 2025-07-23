# drum-processing

[![CI](https://github.com/jvivian/drum-processing/workflows/CI/badge.svg)](https://github.com/jvivian/drum-processing/actions)
[![codecov](https://codecov.io/gh/jvivian/drum-processing/branch/main/graph/badge.svg)](https://codecov.io/gh/jvivian/drum-processing)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Automate drum clip preprocessing

## Installation

### Using Conda (Recommended)

```bash
# Clone the repository
git clone https://github.com/jvivian/drum-processing.git
cd drum-processing

# Create conda environment from environment.yml
conda env create -f environment.yml

# Activate the environment
conda activate drum-processing
```

### Development Installation

For development, use the development environment which includes testing and linting tools:

```bash
# Create development environment
conda env create -f environment-dev.yml

# Activate the environment
conda activate drum-processing-dev

# Install pre-commit hooks
pre-commit install
```

### Using pip

```bash
pip install drum-processing
```

## Quick Start

```python
import drum_processing

# Your code here
```

### Command Line Interface

```bash
drum-processing --help
```

## Features

- Feature 1
- Feature 2
- Feature 3

## Documentation





## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

### Development Setup

1. Fork the repository
2. Create a new branch for your feature
3. Make your changes
4. Run the tests
5. Submit a pull request

### Running Tests

```bash
pytest
```

### Code Formatting



And [Ruff](https://docs.astral.sh/ruff/) for linting:

```bash
ruff check drum_processing/ tests/
```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Authors

- **John Vivian** - *Initial work* - [jvivian](https://github.com/jvivian)

## Acknowledgments

- Hat tip to anyone whose code was used
- Inspiration
- etc