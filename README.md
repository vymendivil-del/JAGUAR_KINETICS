# JAGUAR KINETICS: Biaxial Solar Tracking System with Hierarchical Cognitive Supervisor Control (HCSC)

Repositorio oficial del proyecto experimental presentado en el **4.° Congreso Internacional de Ciencias de Ingeniería, Sociales y Humanidades**.

**Universidad Popular de la Chontalpa (UPCH)** — Cárdenas, Tabasco, México (18.00° N).

---

## 📌 Arquitectura y Descripción del Proyecto
JAGUAR KINETICS desacopla la cinemática electromecánica y la supervisión predictiva para resolver la paradoja del autoconsumo en seguidores fotovoltaicos:
* **Estrato 0 (Edge Determinista):** Microcontrolador Arduino Nano (ATmega328P), algoritmo solar analítico de Spencer (1971) y corte físico < 4 µs ante límite de carrera superior (pin D3) y ráfagas ≥ 45 km/h.
* **Estrato 1 (Gateway IoT):** Telemetría continua V-I-P y persistencia en tiempo real.
* **Estrato 2 (Supervisión Cognitiva HCSC):** Supervisor Gemini 2.5 Flash gobernado por esquema estructurado Pydantic (`JaguarDecision`), modulando ventanas de reposo profundo a 0 V en driver L298N (20 a 60 min).

---

## 📊 Balance Experimental Consolidado (08 al 12 de Octubre de 2026)
Evaluación con módulo monocristalino Epcom EPL1012AL (10 Wp, $A_{\text{neta}} = 0.046764\text{ m}^2$):
* **Panel Fijo (18° Sur):** 268.5 Wh acumulados (Línea base).
* **Seguidor 1 Eje Cenital:** 250.6 Wh (-6.7% vs. fijo por desvío azimutal).
* **Seguidor Biaxial JAGUAR HCSC:** **301.5 Wh (+12.3% de ganancia neta consolidada)** descontando el consumo de los actuadores.

---

## 📚 Referencias Bibliográficas

1. **Spencer, J. W. (1971).** *Fourier series representation of the position of the sun.* Search, 2(5), 172.  
   🔗 [NREL SOLPOS Algorithm](https://www.nrel.gov/grid/solar-resource/solpos)
2. **Shockley, W., & Queisser, H. J. (1961).** *Detailed balance limit of efficiency of p-n junction solar cells.* Journal of Applied Physics, 32(3), 510–519.  
   🔗 [DOI: 10.1063/1.1736034](https://doi.org/10.1063/1.1736034)
3. **Duffie, J. A., & Beckman, W. A. (2013).** *Solar Engineering of Thermal Processes* (4th ed.). John Wiley & Sons.  
   🔗 [DOI: 10.1002/9781118671603](https://doi.org/10.1002/9781118671603)
4. **Pydantic Development Team (2024).** *Data validation and settings management using Python type hints (v2.x).*  
   🔗 [Pydantic Documentation](https://docs.pydantic.dev/)

---

## 👥 Autores y Afiliación Institucional
* **Christian Yahir García Rodríguez**
* **Samanta Liliana Alonso Castellanos**
* **Dr. Omar Sarracino Martinéz**
* **M.C. Noé de la Cruz Santiago**

*Universidad Popular de la Chontalpa (UPCH), Cárdenas, Tabasco.*
