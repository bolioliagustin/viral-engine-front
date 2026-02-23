/** @type {import('jest').Config} */
module.exports = {
    testEnvironment: 'node',
    testMatch: ['**/__tests__/**/*.test.js'],
    collectCoverageFrom: [
        'src/**/*.js',
        '!src/index.js',
        '!src/db/**'
    ],
    // Set test timeout to 10s (some tests mock async operations)
    testTimeout: 10000,
};
