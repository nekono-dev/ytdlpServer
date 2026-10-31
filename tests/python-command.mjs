import { existsSync } from "node:fs"

const repoDir = new URL("..", import.meta.url).pathname

// テストに使う Python の決め方: PYTHON（環境変数）→ リポジトリの .venv → python3 の順
export const pythonCommand = () => {
  if (process.env.PYTHON) return process.env.PYTHON
  const venv = `${repoDir}.venv/bin/python`
  return existsSync(venv) ? venv : "python3"
}
