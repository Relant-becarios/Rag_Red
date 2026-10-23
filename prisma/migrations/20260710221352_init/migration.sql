-- CreateTable
CREATE TABLE "User" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "email" TEXT NOT NULL,
    "nombre" TEXT,
    "rol" TEXT NOT NULL DEFAULT 'CLIENTE',
    "licenciaId" TEXT,
    CONSTRAINT "User_licenciaId_fkey" FOREIGN KEY ("licenciaId") REFERENCES "Licencia" ("id") ON DELETE SET NULL ON UPDATE CASCADE
);

-- CreateTable
CREATE TABLE "Maquina" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "codigoEquipo" TEXT NOT NULL,
    "presupuestoMax" REAL NOT NULL DEFAULT 4000.00,
    "presupuestoUso" REAL NOT NULL DEFAULT 0.00
);

-- CreateTable
CREATE TABLE "Licencia" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "clave" TEXT NOT NULL,
    "maxUsuarios" INTEGER NOT NULL DEFAULT 3,
    "activa" BOOLEAN NOT NULL DEFAULT true,
    "maquinaId" TEXT NOT NULL,
    CONSTRAINT "Licencia_maquinaId_fkey" FOREIGN KEY ("maquinaId") REFERENCES "Maquina" ("id") ON DELETE RESTRICT ON UPDATE CASCADE
);

-- CreateIndex
CREATE UNIQUE INDEX "User_email_key" ON "User"("email");

-- CreateIndex
CREATE UNIQUE INDEX "Maquina_codigoEquipo_key" ON "Maquina"("codigoEquipo");

-- CreateIndex
CREATE UNIQUE INDEX "Licencia_clave_key" ON "Licencia"("clave");

-- CreateIndex
CREATE UNIQUE INDEX "Licencia_maquinaId_key" ON "Licencia"("maquinaId");
