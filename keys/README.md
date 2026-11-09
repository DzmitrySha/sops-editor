# Keys

`age.example.txt` is a **demo** age key. It exists only so that the example files
in `workspace/examples` can be decrypted right after cloning. It is public on
purpose — **never use it for real secrets**.

The application looks for the server-side key at `/keys/age.txt` inside the
container (host path `./keys/age.txt`), or at `SOPS_AGE_KEY_FILE` /
`SOPS_AGE_KEY` when set.

## Quick tour of the examples

```bash
cp keys/age.example.txt keys/age.txt   # Linux/macOS
copy keys\age.example.txt keys\age.txt # Windows
```

## Own key

```bash
age-keygen -o keys/age.txt          # age
sops --age "$(age-keygen -y keys/age.txt)" ...   # put the public key in .sops.yaml
```

`keys/age.txt` is gitignored. You can also skip the server key entirely and paste
`AGE-SECRET-KEY-1…` into the key dialog — it is kept in memory only.
