"""Explicit optional public encoder download. Runtime never downloads models."""
import argparse,hashlib,json,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repository',default='minishlab/potion-multilingual-128M',choices=['minishlab/potion-multilingual-128M','minishlab/potion-base-8M'])
    p.add_argument('--output',type=Path,default=ROOT/'local/models/potion-multilingual-128M')
    args=p.parse_args()
    from huggingface_hub import HfApi,snapshot_download,set_client_factory
    import httpx2
    set_client_factory(lambda:httpx2.Client(trust_env=False,follow_redirects=True,timeout=30))
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True);start=time.monotonic()
    info=HfApi(token=False).model_info(args.repository)
    print(json.dumps({'repository':args.repository,'revision':info.sha,'stage':'downloading_public_weights'}),flush=True)
    names=['config.json','model.safetensors','tokenizer.json','README.md']
    snapshot_download(repo_id=args.repository,revision=info.sha,local_dir=out,allow_patterns=names,token=False,max_workers=3)
    files={name:hashlib.sha256((out/name).read_bytes()).hexdigest() for name in names}
    manifest={'schema':'aether-local-encoder-v1','repository':args.repository,'revision':info.sha,'license':'MIT','files':files,
        'scope':'Public pretrained static embeddings; no local training or hosted embedding API','elapsed_seconds':round(time.monotonic()-start,2)}
    (out/'AETHER_MODEL_MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'stage':'downloaded','bytes':sum((out/name).stat().st_size for name in files),'files':len(files)}),flush=True)

if __name__=='__main__':main()
