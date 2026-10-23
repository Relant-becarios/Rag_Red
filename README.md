# Marketplace Relant + Asistente IA (RAG)

Dos aplicaciones en un mismo repositorio, **cada una con su carpeta y su `.env`**:

```
Rag_Red/
├─ src/, public/, prisma/, package.json …   TIENDA (Vue + Vite)   → Cloudflare / npm run build
│  ├─ .env              variables de la tienda (Prisma, VITE_RAG_API_URL vacía en desarrollo)
│  └─ .env.production   VITE_RAG_API_URL=https://api.relant.com.mx
└─ rag/                                      RAG (FastAPI)          → IIS en C:\Rag_Red
   ├─ app/              código del RAG (incluye /marketplace/* para la tienda)
   ├─ config/ templates/ tools/
   ├─ web.config        IIS → uvicorn app.main:app
   ├─ /data/drive       conocimiento general manuales y archivos para el RAG
   ├─ /db               base de datos chroma del conocimiento del RAG
   └─ .env.example      plantilla del .env del RAG (C:\Rag_Red\.env)
```

## Desarrollo local

```bash
# 1) RAG
cd rag
cp .env.example .env                 # llena OPENROUTER_API_KEY_RELANT, MYSQL_*, MARKETPLACE_*
pip install -r app/requirements.txt
python -m uvicorn app.main:app --port 8000
#   prueba: http://127.0.0.1:8000/marketplace/health  -> "ok": true

# 2) Tienda (otra terminal, en la raíz)
npm install
npm run dev                          # http://localhost:5173 → botón "Asistente IA"
```


## Manuales y conocimientos del RAG deberan colocarse en las carpetas

C:\Rag_Red\rag\data\drive
"# Rag_Red" 
