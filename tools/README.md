# External tools

RefactoringMiner is not vendored. Download the pinned release and point `dde` at its launcher:

```bash
curl -LO https://github.com/tsantalis/RefactoringMiner/releases/download/3.1.6/RefactoringMiner-3.1.6.zip
unzip RefactoringMiner-3.1.6.zip -d tools/
export DDE_REFACTORINGMINER=$PWD/tools/RefactoringMiner-3.1.6/bin/RefactoringMiner
```

It needs Java 17 or later. The version is read from the directory name and recorded in
`run_manifest.json`, so keep the release's directory layout.
