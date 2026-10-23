import express from 'express'
import cors from 'cors'
import { PrismaClient } from '@prisma/client'
import { MercadoPagoConfig, Preference } from 'mercadopago'
import path from 'path';
import { fileURLToPath } from 'url';
import { createProxyMiddleware } from 'http-proxy-middleware';
// Construir __dirname en un entorno ES Module
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const app = express()
const prisma = new PrismaClient()
const PORT = process.env.PORT || 3000


// Configuración del cliente de Mercado Pago
const mpClient = new MercadoPagoConfig({
  accessToken: process.env.MP_ACCESS_TOKEN || 'APP_USR-TUS-CREDENCIALES-AQUI',
})

// URL del RAG (sitio IIS "apimprag"). Se define en web.config -> RAG_URL.
// OJO: en IIS el uvicorn NO escucha en 8021 sino en un puerto aleatorio (%HTTP_PLATFORM_PORT%);
// aquí va el puerto del *binding* del sitio apimprag en IIS.
const RAG_URL = (process.env.RAG_URL || 'http://127.0.0.1:8021').replace(/\/+$/, '')

app.use(cors())

// Proxy al asistente IA. Debe ir ANTES de express.json(): si express.json() lee el body
// primero, el POST /marketplace/chat llega vacío al RAG y la petición se queda colgada.
// Se monta sin ruta (pathFilter) para que el RAG reciba /marketplace/... completo;
// con app.use('/marketplace', ...) Express quita el prefijo y el RAG recibe /chat -> 404.
app.use(
  createProxyMiddleware({
    target: RAG_URL,
    pathFilter: '/marketplace',
    changeOrigin: true,
    proxyTimeout: 180000, // el LLM puede tardar en responder
    timeout: 180000,
    on: {
      error: (err, req, res) => {
        console.error(`[proxy RAG] ${req.method} ${req.url} -> ${RAG_URL}:`, err.message)
        if (res && !res.headersSent && typeof res.writeHead === 'function') {
          res.writeHead(502, { 'Content-Type': 'application/json' })
          res.end(JSON.stringify({ detail: 'El asistente no está disponible en este momento.' }))
        }
      },
    },
  }),
)

app.use(express.json())


// Configuración para servir tu frontend (carpeta 'dist')
app.use(express.static(path.join(__dirname, 'dist')));

// 1. Obtener catálogo completo con filtros de búsqueda y categoría
app.get('/api/products', async (req, res) => {
  try {
    const { search, category } = req.query
    const whereClause = {}

    if (category && category !== 'Todas') {
      whereClause.categoria = String(category)
    }

    if (search) {
      whereClause.OR = [
        { nombre: { contains: String(search), mode: 'insensitive' } },
        { codigo: { contains: String(search), mode: 'insensitive' } },
      ]
    }

    const productos = await prisma.producto.findMany({
      where: whereClause,
      orderBy: { nombre: 'asc' },
    })

    return res.json(productos)
  } catch (error) {
    console.error('Error al consultar productos:', error)
    return res.status(500).json({ error: 'Error interno al consultar el catálogo' })
  }
})

// 2. Generar preferencia de pago en Mercado Pago
app.post('/api/create_preference', async (req, res) => {
  try {
    const { items } = req.body

    if (!items || !Array.isArray(items) || items.length === 0) {
      return res.status(400).json({ error: 'El carrito está vacío' })
    }

    // Mapeo de productos al formato estándar de Mercado Pago
    const bodyItems = items.map((item) => ({
      id: String(item.id),
      title: String(item.nombre),
      quantity: Number(item.cant || item.cantidad),
      unit_price: Number(item.precio),
      currency_id: 'USD', // Ajusta a 'MXN' si cobras en Pesos Mexicanos
    }))

    const preference = new Preference(mpClient)
    const frontendUrl = process.env.FRONTEND_URL || 'http://localhost:5173'

    const result = await preference.create({
      body: {
        items: bodyItems,
        back_urls: {
          success: `${frontendUrl}/checkout?status=approved`,
          failure: `${frontendUrl}/checkout?status=failure`,
          pending: `${frontendUrl}/checkout?status=pending`,
        },
        auto_return: 'approved',
      },
    })

    return res.json({ id: result.id, init_point: result.init_point })
  } catch (error) {
    console.error('Error al crear preferencia en Mercado Pago:', error)
    return res.status(500).json({ error: 'Error al generar la pasarela de pago' })
  }
})

// 3. Transacción de compra atómica (Descuento de stock en PostgreSQL)
app.post('/api/checkout', async (req, res) => {
  const { items } = req.body // Formato esperado: [{ id: 'REL-VAL-HP01', cantidad: 2 }]

  if (!items || !Array.isArray(items) || items.length === 0) {
    return res.status(400).json({ success: false, error: 'El carrito está vacío' })
  }

  try {
    // Garantiza que todas las deducciones ocurran o se reviertan totalmente si falta stock
    await prisma.$transaction(async (tx) => {
      for (const item of items) {
        const idBuscado = String(item.id).trim()

        // Localiza el producto por su ID o por su Código de referencia
        const producto = await tx.producto.findFirst({
          where: {
            OR: [{ id: idBuscado }, { codigo: idBuscado }],
          },
        })

        if (!producto) {
          throw new Error(`Producto no encontrado en inventario: ${item.id}`)
        }

        if (producto.stock < item.cantidad) {
          throw new Error(
            `Stock insuficiente para "${producto.nombre}". Stock disponible: ${producto.stock}, Unidades solicitadas: ${item.cantidad}`,
          )
        }

        // Deducción atómica directa en la base de datos PostgreSQL
        await tx.producto.update({
          where: { id: producto.id },
          data: {
            stock: {
              decrement: Number(item.cantidad),
            },
          },
        })
      }
    })

    return res.json({
      success: true,
      mensaje: 'Pago procesado exitosamente y stock descontado en PostgreSQL.',
    })
  } catch (error) {
    console.error('Error durante la transacción de compra:', error.message)
    return res.status(400).json({ success: false, error: error.message })
  }
})

app.use((req, res) => {
  res.sendFile(path.join(__dirname, 'dist', 'index.html'));
});

app.listen(PORT, () => {
  console.log(`Servidor de inventario corriendo en el puerto ${PORT} (RAG: ${RAG_URL})`)
})

export default app
