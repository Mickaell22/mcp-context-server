<div align="center">

<img src="./docs/screenshots/title.svg" alt="MCP Context Server" width="560"/>

### Memoria de tus proyectos para Claude Code: responde con lo relevante, no con archivos enteros.

<br/>

<img src="./docs/screenshots/hero.svg" alt="Flujo: los archivos del repo pasan por ChromaDB y DeepSeek y a Claude Code llega solo el contexto relevante" width="900"/>

<br/><br/>

[![13 Tools MCP](https://img.shields.io/badge/13-Tools_MCP-6C5CE7?style=for-the-badge)](#que-puede-hacer)
[![Contexto comprimido](https://img.shields.io/badge/Contexto-Comprimido-00B894?style=for-the-badge)](#por-que)
[![Auditoria de codigo](https://img.shields.io/badge/Auditoria-de_codigo-E17055?style=for-the-badge)](#que-puede-hacer)
[![Local o multi-equipo](https://img.shields.io/badge/Local_o-multi--equipo-0984E3?style=for-the-badge)](#instalacion)

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![MCP](https://img.shields.io/badge/MCP-SDK-blueviolet?style=flat-square)](https://modelcontextprotocol.io)
[![DeepSeek](https://img.shields.io/badge/DeepSeek-Flash-4D6BFE?style=flat-square)](https://platform.deepseek.com)
[![ChromaDB](https://img.shields.io/badge/ChromaDB-vectores-FF6B35?style=flat-square)](https://trychroma.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-metricas-4169E1?style=flat-square&logo=postgresql&logoColor=white)](https://www.postgresql.org)
[![Stars](https://img.shields.io/github/stars/Mickaell22/mcp-context-server?style=flat-square&logo=github)](https://github.com/Mickaell22/mcp-context-server)

[**Por que**](#por-que) • [**Que puede hacer**](#que-puede-hacer) • [**Instalacion**](#instalacion) • [**Documentacion**](#documentacion) • [**Dashboard**](https://ia.novamicktools.com)

</div>

---

## Por que

Cuando Claude Code necesita entender una parte del proyecto, lee archivos completos y cada linea cuenta como contexto. mcp-context indexa el repo una vez, busca los fragmentos que importan y le pasa a Claude Code **un resumen con las lineas exactas** donde mirar.

<p align="center">
  <img src="./docs/screenshots/ahorro.svg" alt="Consulta real: unos 12.100 tokens leyendo los archivos enteros contra unos 770 con mcp-context" width="900"/>
</p>

El resumen lo hace un modelo barato (DeepSeek Flash): una consulta cuesta una fraccion de centavo y una auditoria completa ronda los 1-2 centavos.

---

## Que puede hacer

<p align="center">
  <img src="./docs/screenshots/tools.svg" alt="13 tools en 4 grupos: Consultar, Indexar, Auditar y Mantener" width="900"/>
</p>

Parametros y detalle de cada una en **[docs/TOOLS.md](docs/TOOLS.md)**.

---

## Instalacion

Lo mas facil es que lo instale Claude Code. Abre Claude Code en la carpeta del repo y pidele:

```text
Lee docs/INSTALACION.md e instala mcp-context-server
```

Te va a preguntar como lo vas a usar y elige el modo:

<table>
<tr>
<td width="50%" valign="top">

### Un solo equipo

**Modo local.** La base de datos corre en tu propio equipo (Docker o la Postgres del sistema) y nada sale de el, salvo las llamadas a DeepSeek y los `git fetch` a GitHub.

Recomendado para empezar.

</td>
<td width="50%" valign="top">

### Varios equipos

**Modo multi-equipo.** Todos apuntan a la misma Postgres (Railway, Neon, Supabase o un server tuyo por Tailscale) y comparten proyectos, historial e indice de imports.

Se puede pasar de local a multi-equipo despues, sin perder nada.

</td>
</tr>
</table>

Necesitas Python 3.11+, git y una API key de [DeepSeek](https://platform.deepseek.com). Los pasos a mano estan en la misma [guia de instalacion](docs/INSTALACION.md).

---

## Documentacion

<table>
<tr>
<td width="50%" valign="top">

**[Instalacion](docs/INSTALACION.md)**<br/>
Los dos modos paso a paso, registro en Claude Code y verificacion.

</td>
<td width="50%" valign="top">

**[Tools](docs/TOOLS.md)**<br/>
Las 13 tools con todos sus parametros.

</td>
</tr>
<tr>
<td valign="top">

**[Configuracion](docs/CONFIGURACION.md)**<br/>
Variables de entorno y costos de DeepSeek por franja horaria.

</td>
<td valign="top">

**[Varios equipos](docs/MULTI-EQUIPO.md)**<br/>
Que se comparte, rutas por dispositivo y como mantener todos al dia.

</td>
</tr>
<tr>
<td valign="top" colspan="2">

**[Desarrollo](docs/DESARROLLO.md)**<br/>
Estructura del codigo, stack, tests y evaluacion del audit con bugs sembrados.

</td>
</tr>
</table>

---

## Seguridad

- **Solo lectura sobre tus proyectos.** Indexar y auditar leen los archivos en su sitio; el server solo escribe en la carpeta donde clona repos.
- **Nunca indexa secretos.** Ignora `.env`, llaves y certificados, lockfiles, minificados y todo lo que diga tu `.gitignore`.
- **Solo ejecuta git** (y `pip`, al actualizarse a si mismo si cambiaron sus dependencias). El token de GitHub viaja en memoria y nunca queda escrito en `.git/config`.
- **No se expone a internet.** Corre como proceso local de Claude Code; para varios equipos, por Tailscale.
