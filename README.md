# OCR Image Reader

API de OCR para receitas médicas usando [Tesseract](https://tesseractocr.org/).  
Consumida pelo endpoint `PlaceQuotation` do e-commerce quando houver anexo de receita.

## Fluxo

1. Cliente (CRM/ChatBot) chama `PlaceQuotation` com anexo de receita.
2. O e-commerce chama `POST /ocr` nesta API.
3. A API devolve o texto extraído da imagem.
4. O e-commerce usa o texto no orçamento.

## Requisitos

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
| `TESSERACT_CMD` | Path do binário Tesseract | (auto) |
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

## Docker

```bash
docker build -t ocr-image-reader .
docker run --rm -p 8080:8080 -e OCR_API_KEY=sua-chave ocr-image-reader
```

## Contrato da API

### `GET /health`

Sem autenticação. Retorna status e versão do Tesseract.

```json
{ "status": "ok", "tesseract": "5.3.0" }
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

#### Opção B — URL (padrão do CRM com `attachmentFileUrls`)

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

## Integração com PlaceQuotation

No e-commerce, ao processar anexos de receita:

1. Se o anexo for arquivo local/upload → `multipart` com campo `file`.
2. Se o anexo for URL (como `attachmentFileUrls` do CRM) → JSON com `imageUrl`.
3. Incluir o texto retornado na mensagem/itens do orçamento conforme a regra de negócio da loja.

Exemplo (C# / HttpClient):

```csharp
using var request = new HttpRequestMessage(HttpMethod.Post, $"{ocrBaseUrl}/ocr");
request.Headers.Add("X-Api-Key", ocrApiKey);
request.Content = JsonContent.Create(new { imageUrl = attachmentUrl });
var response = await httpClient.SendAsync(request);
var ocr = await response.Content.ReadFromJsonAsync<OcrResponse>();
// usar ocr.Text
```

## Limitações

- MVP devolve **texto bruto** (sem parsing de medicamento/dose).
- Receitas **impressas** tendem a ter boa acurácia; **manuscritas** têm acurácia menor.
- Melhor resultado com imagens nítidas, alto contraste e ~300 DPI.

## Testes

```bash
pytest -q
```
