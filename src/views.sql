-- Las dos vistas del enunciado. Son lo UNICO que la API puede consultar:
-- el validador de SQL lleva una allowlist con estos dos nombres, asi que la
-- tabla base `orders` y la de rechazos quedan fuera del alcance del LLM.
--
-- CREATE OR REPLACE para que el ETL sea idempotente: se puede relanzar sin
-- borrar la base de datos a mano.

-- Ventas por mes y pais.
-- Decision: `cancelado` y `devuelto` NO cuentan como venta. Un pedido cancelado
-- esta en la tabla (es un hecho que ocurrio) pero sumarlo inflaria los ingresos.
CREATE OR REPLACE VIEW v_ventas_mes_pais AS
SELECT
    date_trunc('month', fecha)          AS mes,
    pais,
    count(*)                            AS num_pedidos,
    round(sum(importe), 2)              AS importe_total,
    round(avg(importe), 2)              AS ticket_medio
FROM orders
WHERE estado NOT IN ('cancelado', 'devuelto')
  AND importe IS NOT NULL          -- los importes vacios no suman ni restan
GROUP BY 1, 2
ORDER BY 1, 2;

-- Top 10 clientes por facturacion, con el mismo criterio de venta efectiva.
CREATE OR REPLACE VIEW v_top_clientes AS
SELECT
    cliente,
    count(*)                            AS num_pedidos,
    round(sum(importe), 2)              AS importe_total,
    round(avg(importe), 2)              AS ticket_medio,
    max(fecha)                          AS ultima_compra
FROM orders
WHERE estado NOT IN ('cancelado', 'devuelto')
  AND importe IS NOT NULL
GROUP BY 1
ORDER BY importe_total DESC
LIMIT 10;
