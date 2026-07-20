# OCR Image Reader

API de OCR para receitas médicas usando [Tesseract](https://tesseractocr.org/) com pré-processamento (binarização, deskew, denoise) e fallback opcional para **OpenAI Vision** (manuscritos / baixa confiança).

## Requisitos (local)

- Python 3.12+
- Binário [Tesseract OCR](https://tesseractocr.org/) com idioma `por`
  - Windows: instalador UB Mannheim + PATH ou `TESSERACT_CMD`
  - Linux: `sudo apt install tesseract-ocr tesseract-ocr-por`
- (Opcional) `OPENAI_API_KEY` para fallback Vision / motor `openai`

## Configuração

```bash
cp .env.example .env
# edite OCR_API_KEY e, se for usar Vision/estruturação, OPENAI_API_KEY
```

| Variável | Descrição | Default |
|----------|-----------|---------|
| `OCR_API_KEY` | Chave do header `X-Api-Key` | `change-me-to-a-secure-key` |
| `TESSERACT_CMD` | Path do binário Tesseract (só Windows/local) | (auto) |
| `OCR_LANGUAGE` | Idioma do modelo | `por` |
| `MAX_IMAGE_BYTES` | Limite do arquivo | `10485760` (10MB) |
| `DOWNLOAD_TIMEOUT_SECONDS` | Timeout ao baixar `imageUrl` | `30` |
| `OCR_ENGINE` | `auto` \| `tesseract` \| `openai` | `auto` |
| `OCR_CONFIDENCE_THRESHOLD` | Confiança mínima Tesseract (0–100) para aceitar sem Vision | `60` |
| `OPENAI_API_KEY` | Chave OpenAI (Vision) | (vazio) |
| `OPENAI_BASE_URL` | Base URL da API | `https://api.openai.com/v1` |
| `OPENAI_VISION_MODEL` | Modelo de visão | `gpt-4o-mini` |
| `OPENAI_MAX_TOKENS` | Máx. tokens da resposta | `2000` |
| `OPENAI_TIMEOUT_SECONDS` | Timeout da chamada Vision/estrutura | `60` |
| `OCR_STRUCTURE_ENABLED` | Estrutura receita via OpenAI após OCR | `true` |
| `OPENAI_STRUCTURE_MODEL` | Modelo para estruturação | `gpt-4o-mini` |
| `OPENAI_STRUCTURE_MAX_TOKENS` | Máx. tokens da estruturação | `1500` |

## Executar local

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt
uvicorn app.main:app --reload --port 8080
```

Docs interativas: http://localhost:8080/docs

## Docker (standalone)

```bash
docker build -t ocr-image-reader:latest .
docker run --rm -p 8080:8080 \
  -e OCR_API_KEY=sua-chave \
  -e OPENAI_API_KEY=sk-... \
  ocr-image-reader:latest
```

## Deploy nas VPS (Traefik + Docker Swarm)

Infra das VPS IntegraPedidos (SP e AMS):

| Item | Valor |
|------|-------|
| Proxy | Traefik (Docker Swarm) |
| Rede overlay | `IntegraPedidosNet` |
| Entrypoint TLS | `websecure` |
| Cert resolver | `letsencryptresolver` |
| Porta do container | `8080` |

| Ambiente | Domínio | Compose |
|----------|---------|---------|
| VPS São Paulo | `https://ocr.integrapedidos.com.br` | [docker-compose.ocr.yml](docker-compose.ocr.yml) |
| VPS Amsterdam | `https://ocr2.integrapedidos.com.br` | [docker-compose.ocr2.yml](docker-compose.ocr2.yml) |

DNS: `ocr` → IP da VPS SP, `ocr2` → IP da VPS AMS. No Cloudflare, use SSL **Full (strict)** se o proxy estiver laranja.

**Envs no Swarm:** o compose usa `${VAR}` resolvido no momento do `stack deploy`. Faça `export` no shell da VPS (ou `set -a; source .env; set +a`). Um arquivo `.env` no disco **não** é injetado sozinho no serviço.

Na SP use só `docker-compose.ocr.yml`; na AMS, só `docker-compose.ocr2.yml`. Os dois arquivos existem no clone em qualquer VPS — isso é normal.

### 1. Build da imagem (em cada VPS)

```bash
cd /root
git clone https://github.com/renatogava/ocr-image-reader.git
# ou: cd /root/ocr-image-reader && git pull
cd ocr-image-reader
docker build -t ocr-image-reader:latest .
```

Se o `git pull` falhar por compose local untracked (ex.: `docker-compose.ocr.yml` já existia fora do git), mova o backup e puxe de novo:

```bash
mv docker-compose.ocr.yml docker-compose.ocr.yml.bak   # se necessário
git pull
# depois: rm docker-compose.ocr.yml.bak
```

### 2. Definir envs e subir o stack

A mesma `OCR_API_KEY` / `OPENAI_API_KEY` pode ser usada nas duas VPS.

**SP:**

```bash
export OCR_API_KEY='sua-chave-forte'
export OPENAI_API_KEY='sk-...'                 # Vision + estruturação
export OCR_ENGINE=auto                         # opcional (default no compose)
export OCR_STRUCTURE_ENABLED=true              # opcional (default true)
docker stack deploy -c docker-compose.ocr.yml ocr
```

**AMS** (só muda o arquivo de compose / Host):

```bash
export OCR_API_KEY='sua-chave-forte'
export OPENAI_API_KEY='sk-...'
export OCR_ENGINE=auto
export OCR_STRUCTURE_ENABLED=true
docker stack deploy -c docker-compose.ocr2.yml ocr
```

Sem `OPENAI_API_KEY`, o serviço sobe, mas `openai_configured` fica `false` (sem Vision/estruturação).

### 3. Conferir o serviço

```bash
docker service ls | grep ocr
docker service ps ocr_ocr
docker service logs ocr_ocr --tail 50
```

Aviso `image could not be accessed on a registry` com imagem só local é esperado; use `--force` no update se a task não recarregar a imagem nova.

### 4. Smoke test

```bash
# Health (sem auth)
curl -s https://ocr.integrapedidos.com.br/health
curl -s https://ocr2.integrapedidos.com.br/health

# OCR com URL pública da imagem
curl -X POST https://ocr.integrapedidos.com.br/ocr \
  -H "X-Api-Key: SUA_CHAVE" \
  -H "Content-Type: application/json" \
  -d '{"imageUrl":"https://URL_PUBLICA_RECEITA.jpg"}'

# Repetir o POST em ocr2 após o deploy na AMS
curl -X POST https://ocr2.integrapedidos.com.br/ocr \
  -H "X-Api-Key: SUA_CHAVE" \
  -H "Content-Type: application/json" \
  -d '{"imageUrl":"https://URL_PUBLICA_RECEITA.jpg"}'
```

Resposta esperada do health (versão do Tesseract pode variar):

```json
{ "status": "ok", "tesseract": "5.5.0", "openai_configured": true }
```

Se `openai_configured` estiver ausente, a imagem em execução ainda é antiga — faça rebuild e redeploy.

### 5. Atualizar após `git pull`

```bash
cd /root/ocr-image-reader
git pull
docker build --no-cache -t ocr-image-reader:latest .

# reexportar envs na mesma sessão (obrigatório se mudou chave / engine)
export OCR_API_KEY='sua-chave-forte'
export OPENAI_API_KEY='sk-...'

# SP — reaplicar compose (atualiza envs + imagem)
docker stack deploy -c docker-compose.ocr.yml ocr
# AMS: docker stack deploy -c docker-compose.ocr2.yml ocr

# se a task não trocar de imagem:
docker service update --force ocr_ocr
```

Alternativa só de imagem (sem mudar envs): `docker service update --image ocr-image-reader:latest --force ocr_ocr`.

### Troubleshooting Traefik

1. Task na rede: `docker service ps ocr_ocr`
2. Logs Traefik: `docker service logs traefik_traefik --tail 100`
3. DNS apontando para o IP correto da VPS
4. Aguardar 1–2 min para o Let's Encrypt emitir o certificado
5. Health sem `openai_configured` → rebuild `--no-cache` + `stack deploy` com `OPENAI_API_KEY` exportada

## Contrato da API

### `GET /health`

Sem autenticação. Retorna status do Tesseract e se a Vision está configurada.

```json
{ "status": "ok", "tesseract": "5.5.0", "openai_configured": true }
```

### `POST /ocr`

Exige header `X-Api-Key`.

Query opcional: `?engine=auto|tesseract|openai` (sobrescreve `OCR_ENGINE`).

#### Fluxo dos motores

| Engine | Comportamento |
|--------|----------------|
| `tesseract` | Só Tesseract (com preprocess OpenCV) |
| `openai` | Só OpenAI Vision (imagem original) |
| `auto` | Tesseract; se confiança &lt; limiar, texto vazio ou sem conf → Vision (se `OPENAI_API_KEY` existir) |

#### Opção A — upload multipart

```http
POST /ocr
X-Api-Key: sua-chave
Content-Type: multipart/form-data

file: <imagem PNG/JPEG/WebP/TIFF>
engine: auto   # opcional
```

#### Opção B — URL pública

```http
POST /ocr
X-Api-Key: sua-chave
Content-Type: application/json

{ "imageUrl": "https://cdn.exemplo.com/receita.jpg", "engine": "auto" }
```

#### Resposta de sucesso

```json
{
  "success": true,
  "text": "Dipirona 500mg\n1 comprimido a cada 6 horas...",
  "language": "por",
  "confidence": 87.5,
  "engine": "tesseract",
  "structuredBy": "openai",
  "structured": {
    "date": "19/07/2026",
    "header": "Dr. Fulano de Tal\nCRM 12345\nClínica Exemplo",
    "patient": "Maria Silva",
    "inscription": "Dipirona 500mg comprimido",
    "posology": "1 comprimido a cada 6 horas",
    "items": [
      {
        "drugName": "Dipirona",
        "pharmaceuticalForm": "comprimido",
        "concentration": "500mg",
        "posology": "1 comprimido a cada 6 horas"
      }
    ]
  }
}
```

`confidence` com `engine=openai` é uma **estimativa** (autoavaliação do modelo + penalidade por trechos `[ilegível]`), não a métrica estatística do Tesseract.  
`structured` / `structuredBy` vêm preenchidos quando há `OPENAI_API_KEY` e estruturação habilitada (`OCR_STRUCTURE_ENABLED=true`, default). Use `?structure=false` para desligar na requisição.

#### Erros

| HTTP | Situação |
|------|----------|
| `401` | API key inválida ou ausente |
| `400` | Sem imagem / Content-Type inválido / formato inválido |
| `422` | OCR sem texto / imagem ilegível |
| `502` | Falha ao baixar `imageUrl` ou chamar Vision/estruturação |
| `503` | `engine=openai` / `structure=true` sem `OPENAI_API_KEY` |

## Limitações

- O campo `text` continua sendo o OCR bruto; `structured` é a organização via LLM.
- Receitas **impressas**: Tesseract + preprocess costuma bastar.
- Receitas **manuscritas**: use `OCR_ENGINE=auto` (ou `openai`) com `OPENAI_API_KEY`.
- Com Vision, `confidence` é estimativa do modelo (0–100), ajustada se houver `[ilegível]`.
- Melhor resultado com imagens nítidas; preprocess ajuda em scans tortos/ruidosos.
- Não logamos conteúdo de receita/imagem; trate `OPENAI_API_KEY` e dados sensíveis com cuidado (LGPD).

## Testes

```bash
pytest -q
```
