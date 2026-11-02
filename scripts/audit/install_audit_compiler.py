"""Install an official Solidity compiler only in this project's audit tooling directory."""
import os
from pathlib import Path
import solcx
root=Path(__file__).resolve().parents[2];path=root/'.audit-tools/solc';path.mkdir(parents=True,exist_ok=True)
os.environ['SOLCX_BINARY_PATH']=str(path)
version='0.8.28'
solcx.install_solc(version,solcx_binary_path=path)
print({'compiler':version,'directory':str(path)})
