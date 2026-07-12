# OCR Image Reader

API de OCR para receitas médicas usando [Tesseract](https://tesseractocr.org/).

## Requisitos (local)

- Python 3.12+
- Binário [Tesseract OCR](https://tesseractocr.org/) com idioma `por`
  - Windows: instalador UB Mannheim + PATH ou `TESSERACT_CMD`
  - Linux: `sudo apt install tesseract-ocr tesseract-ocr-por`

## Configuração

```bash
cp .env.example .env
# edite OCR_API_KEY
```

| Variável | Descrição | Default |
|----------|-----------|---------|
| `OCR_API_KEY` | Chave do header `X-Api-Key` | `change-me-to-a-secure-key` |
| `TESSERACT_CMD` | Path do binário Tesseract (só Windows/local) | (auto) |
| `OCR_LANGUAGE` | Idioma do modelo | `por` |
| `MAX_IMAGE_BYTES` | Limite do arquivo | `10485760` (10MB) |
| `DOWNLOAD_TIMEOUT_SECONDS` | Timeout ao baixar `imageUrl` | `30` |

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
docker run --rm -p 8080:8080 -e OCR_API_KEY=sua-chave ocr-image-reader:latest
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

### 1. Build da imagem (em cada VPS)

```bash
cd /root
git clone https://github.com/renatogava/ocr-image-reader.git
# ou: cd /root/ocr-image-reader && git pull
cd ocr-image-reader
docker build -t ocr-image-reader:latest .
```

### 2. Definir a API key e subir o stack

A mesma `OCR_API_KEY` pode ser usada nas duas VPS.

**SP:**

```bash
export OCR_API_KEY='sua-chave-forte'
docker stack deploy -c docker-compose.ocr.yml ocr
```

**AMS** (só muda o arquivo de compose / Host):

```bash
export OCR_API_KEY='sua-chave-forte'
docker stack deploy -c docker-compose.ocr2.yml ocr
```

### 3. Conferir o serviço

```bash
docker service ls | grep ocr
docker service ps ocr_ocr
docker service logs ocr_ocr --tail 50
```

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

Resposta esperada do health: `{"status":"ok","tesseract":"5.5.0"}` (versão pode variar).

### 5. Atualizar após `git pull`

```bash
cd /root/ocr-image-reader
git pull
docker build -t ocr-image-reader:latest .
docker service update --image ocr-image-reader:latest ocr_ocr
# se a imagem for apenas local e o serviço não puxar:
# docker service update --force ocr_ocr
```

### Troubleshooting Traefik

1. Task na rede: `docker service ps ocr_ocr`
2. Logs Traefik: `docker service logs traefik_traefik --tail 100`
3. DNS apontando para o IP correto da VPS
4. Aguardar 1–2 min para o Let's Encrypt emitir o certificado

## Contrato da API

### `GET /health`

Sem autenticação. Retorna status e versão do Tesseract.

```json
{ "status": "ok", "tesseract": "5.5.0" }
```

### `POST /ocr`

Exige header `X-Api-Key`.

#### Opção A — upload multipart

```http
POST /ocr
X-Api-Key: sua-chave
Content-Type: multipart/form-data

file: <imagem PNG/JPEG/WebP/TIFF>
```

#### Opção B — URL pública

```http
POST /ocr
X-Api-Key: sua-chave
Content-Type: application/json

{ "imageUrl": "https://cdn.exemplo.com/receita.jpg" }
```

#### Resposta de sucesso

```json
{
  "success": true,
  "text": "Dipirona 500mg\n1 comprimido a cada 6 horas...",
  "language": "por",
  "confidence": 87.5
}
```

#### Erros

| HTTP | Situação |
|------|----------|
| `401` | API key inválida ou ausente |
| `400` | Sem imagem / Content-Type inválido / formato inválido |
| `422` | OCR sem texto / imagem ilegível |
| `502` | Falha ao baixar `imageUrl` |

## Limitações

- MVP devolve **texto bruto** (sem parsing de medicamento/dose).
- Receitas **impressas** tendem a ter boa acurácia; **manuscritas** têm acurácia menor.
- Melhor resultado com imagens nítidas, alto contraste e ~300 DPI.

## Testes

```bash
pytest -q
```
