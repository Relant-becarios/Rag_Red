import { initializeApp } from 'firebase/app'
import { getDatabase, ref, onValue } from 'firebase/database'
import { getAuth } from 'firebase/auth'

const firebaseConfig = {
  databaseURL: 'https://relantmarketplace-default-rtdb.firebaseio.com',
}

const app = initializeApp(firebaseConfig)

// Exportamos explícitamente db y auth
export const db = getDatabase(app)
export const auth = getAuth(app)

export const conectarFirebase = (callback: (data: unknown) => void) => {
  onValue(ref(db, 'productos'), (snapshot) => {
    callback(snapshot.val() || [])
  })
}
