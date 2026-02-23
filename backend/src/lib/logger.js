/**
 * S6: Structured Logging with Winston
 * Provides JSON-formatted logs for production and colored console output for development.
 * All logs include timestamp, level, service name, and contextual metadata.
 */
const winston = require('winston');

const isProduction = process.env.NODE_ENV === 'production';

const logger = winston.createLogger({
    level: process.env.LOG_LEVEL || (isProduction ? 'info' : 'debug'),
    defaultMeta: { service: 'viralengine-api' },
    format: winston.format.combine(
        winston.format.timestamp({ format: 'YYYY-MM-DD HH:mm:ss.SSS' }),
        winston.format.errors({ stack: true }),
        isProduction
            ? winston.format.json()
            : winston.format.combine(
                winston.format.colorize(),
                winston.format.printf(({ timestamp, level, message, service, ...meta }) => {
                    const metaStr = Object.keys(meta).length ? ` ${JSON.stringify(meta)}` : '';
                    return `${timestamp} [${service}] ${level}: ${message}${metaStr}`;
                })
            )
    ),
    transports: [
        new winston.transports.Console(),
    ],
});

// Add file transport in production
if (isProduction) {
    logger.add(new winston.transports.File({
        filename: 'logs/error.log',
        level: 'error',
        maxsize: 10 * 1024 * 1024, // 10MB
        maxFiles: 5,
    }));
    logger.add(new winston.transports.File({
        filename: 'logs/combined.log',
        maxsize: 10 * 1024 * 1024,
        maxFiles: 5,
    }));
}

module.exports = logger;
