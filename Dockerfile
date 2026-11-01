# 后端
FROM maven:3.9-eclipse-temurin-17 AS backend
WORKDIR /build
COPY backend/pom.xml .
RUN mvn dependency:go-offline -B
COPY backend/src ./src
RUN mvn package -DskipTests -B

# 前端
FROM node:20-alpine AS frontend
WORKDIR /fe
COPY frontend/package*.json ./
RUN npm ci --registry=https://registry.npmmirror.com
COPY frontend/ ./
RUN npm run build

# 运行时
FROM eclipse-temurin:17-jre
WORKDIR /app
COPY --from=backend /build/target/*.jar app.jar
COPY --from=frontend /fe/dist ./static
EXPOSE 8080
CMD ["java", "-jar", "app.jar"]