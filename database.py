import aiomysql
import asyncio

from dotenv import load_dotenv

import os

load_dotenv()

class Database:
    def __init__(self, cog: str, loop: asyncio.AbstractEventLoop):
        self.config = {
            'user': os.getenv("USER_DB"),
            'password': os.getenv("PASSWORD_DB"),
            'host': os.getenv("HOST"),
            'port': 3307,
            'db': os.getenv("DATABASE_NAME"),
            'autocommit': False
        }
        self.cog = cog
        self.loop = loop
        self.pool = None

    async def connect(self):
        """Creates a MySQL connection pool."""
        try:
            self.pool = await aiomysql.create_pool(
                minsize=1,
                maxsize=10,
                loop=self.loop,
                **self.config
            )
            print(f"MySQL pool connection in {self.cog} established.")
            await self.create_tables()
        except Exception as e:
            print(f"Error connecting to MySQL in {self.cog}: {e}")
            raise

    async def close(self):
        """Closes the connection pool."""
        if self.pool:
            self.pool.close()
            await self.pool.wait_closed()
            print(f"MySQL pool in {self.cog} closed.")

    async def create_tables(self):
        """Создаёт нужные таблицы (пример, можешь изменить под себя)."""
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                # --- Creates tables for moderating if they do not exist.---
                warnings_table = """
                CREATE TABLE IF NOT EXISTS warnings (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    user_id BIGINT NOT NULL,
                    guild_id BIGINT NOT NULL,
                    moderator_id BIGINT NOT NULL,
                    reason TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    ended_at TEXT
                );
                """  # Creating warnings table
                punishments_table = """
                CREATE TABLE IF NOT EXISTS punishments (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    user_id BIGINT NOT NULL,
                    guild_id BIGINT NOT NULL,
                    action TEXT NOT NULL,
                    ends_at TEXT
                );
                """  # Creating punishments table
                await cursor.execute(warnings_table)
                await cursor.execute(punishments_table)
                await conn.commit()

    # --- Punishments and warnings ---
    async def get_all_active_punishments(self):
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    SELECT user_id, guild_id, action, ends_at 
                    FROM punishments 
                    WHERE ends_at IS NOT NULL
                """)
                return await cursor.fetchall()

    async def delete_punishment(self, user_id: int, guild_id: int, action: str):
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    DELETE FROM punishments 
                    WHERE user_id = %s AND guild_id = %s AND action = %s
                """, (user_id, guild_id, action))
                await conn.commit()

    async def add_warning(self, user_id: int, guild_id: int, moderator_id: int, reason: str, timestamp: str):
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    INSERT INTO warnings 
                    (user_id, guild_id, moderator_id, reason, timestamp) 
                    VALUES (%s, %s, %s, %s, %s)
                """, (user_id, guild_id, moderator_id, reason, timestamp))
                await conn.commit()

    async def get_warnings_count(self, user_id: int, guild_id: int) -> int:
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    SELECT COUNT(*) FROM warnings 
                    WHERE user_id = %s AND guild_id = %s
                """, (user_id, guild_id))
                result = await cursor.fetchone()
                return result[0] if result else 0

    async def get_all_user_warnings(self, user_id: int, guild_id: int):
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    SELECT reason, timestamp, moderator_id 
                    FROM warnings 
                    WHERE user_id = %s AND guild_id = %s
                """, (user_id, guild_id))
                return await cursor.fetchall()

    async def get_one_user_warning(self, user_id: int, guild_id: int):
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    SELECT id 
                    FROM warnings 
                    WHERE user_id = %s AND guild_id = %s 
                    ORDER BY id DESC LIMIT 1
                """, (user_id, guild_id))
                return await cursor.fetchone()

    async def delete_last_warning(self, warning_id: int):
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    DELETE FROM warnings 
                    WHERE id = %s
                """, (warning_id,))
                await conn.commit()

    async def delete_all_user_warnings(self, user_id: int, guild_id: int):
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    DELETE FROM warnings 
                    WHERE user_id = %s AND guild_id = %s
                """, (user_id, guild_id,))
                await conn.commit()