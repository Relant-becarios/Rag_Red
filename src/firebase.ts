import { initializeApp } from 'firebase/app'
import { getDatabase, ref, onValue } from 'firebase/database'
import { getAuth } from 'firebase/auth'
import { getAnalytics } from 'firebase/analytics'

// Tus llaves reales de Firebase
const firebaseConfig = {
  apiKey: 'AIzaSyBWI5Gt4rpzbC9ROFJl99ivCjlhecRaYYg',
  authDomain: 'relantmarketplace.firebaseapp.com',
  databaseURL: 'https://relantmarketplace-default-rtdb.firebaseio.com',
  projectId: 'relantmarketplace',
  storageBucket: 'relantmarketplace.firebasestorage.app',
  messagingSenderId: '331114840284',
  appId: '1:331114840284:web:9a33b20d0185722ec97419',
  measurementId: 'G-56GYWVH01H',
}

// Inicializamos Firebase con tu configuración
const app = initializeApp(firebaseConfig)
const _analytics = getAnalytics(app) // Opcional, pero bueno tenerlo ya que te lo dio Firebase

// Exportamos explícitamente db y auth para que el resto de tu app las use
export const db = getDatabase(app)
export const auth = getAuth(app)

export const conectarFirebase = (callback: (data: unknown) => void) => {
  onValue(ref(db, 'productos'), (snapshot) => {
    callback(snapshot.val() || [])
  })
}
